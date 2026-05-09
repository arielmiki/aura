"""Gemini Flash brain with remember(fact) tool dispatch.

Builds the system prompt from persona template + memories + recent conversation,
calls Gemini once, and loops on any tool calls (executing remember and feeding
the response back) until the model produces a text response.

Two entry points:
  - respond(transcript, image, ...): legacy text-input path (used as fallback
    when STT runs separately).
  - respond_audio(audio_bytes, mime, image, ...): audio-native path that skips
    a separate STT call. The model both "hears" the user and replies in one
    multimodal call, returning (user_said, reply). Drops ~700ms of latency.
"""
from __future__ import annotations

import logging
import os
import re
from pathlib import Path
from typing import Awaitable, Callable, Optional, Tuple

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

# Default text-only model (used by respond()).
LLM_MODEL = os.environ.get("BRAIN_MODEL", "gemini-2.5-flash")
# Audio-native model (used by respond_audio()). Smaller / faster Flash Lite
# variant — tuned for low-latency conversational turns.
LLM_MODEL_AUDIO = os.environ.get("BRAIN_AUDIO_MODEL", "gemini-3.1-flash-lite-preview")

# Convention used by respond_audio() to extract a transcript from the model's
# reply so the UI can still show YOU said "...". Kept tag-based (not JSON) so
# we can coexist with tool calls (remember / recall_visual) which don't play
# nice with response_schema.
TRANSCRIPT_TAG_RE = re.compile(r"\[YOU_SAID\]\s*(.*?)\s*(?:\n|\[REPLY\]|$)", re.S)
REPLY_TAG_RE      = re.compile(r"\[REPLY\]\s*(.*)\s*$",                    re.S)
AUDIO_FORMAT_INSTRUCTION = (
    "\n\n# RESPONSE FORMAT (STRICT — applies whenever the user message is audio)\n"
    "You are listening to the user via audio. Always begin your reply with "
    "two tags, in this exact order, on their own lines:\n"
    "[YOU_SAID] <a verbatim, single-line transcription of what the user just said>\n"
    "[REPLY] <your spoken reply — only this part is read aloud>\n"
    "Tool calls (remember, recall_visual) may still happen normally; the tags "
    "appear in your final text turn after any tool calls finish.\n"
    "\n# MEMORY USE (NON-NEGOTIABLE)\n"
    "The 'ACTIVE MEMORY' block you'll see WITH each user message lists facts "
    "you already know about this person. You MUST reference these facts when "
    "relevant: use the user's name, recall their preferences, mention their "
    "pets/family/work by name, build on prior topics. NEVER ask for "
    "information that ACTIVE MEMORY already gives you. If a memory matches "
    "the topic, weave it in naturally — don't pretend you've forgotten."
)

REMEMBER_TOOL = types.Tool(function_declarations=[types.FunctionDeclaration(
    name="remember",
    description="Save a fact about the user to long-term memory.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "fact": types.Schema(
                type=types.Type.STRING,
                description="A one-sentence fact about the user, e.g. 'User has a corgi named Lily.'",
            ),
        },
        required=["fact"],
    ),
)])


RECALL_VISUAL_TOOL = types.Tool(function_declarations=[types.FunctionDeclaration(
    name="recall_visual",
    description=(
        "Retrieve a past visual memory by topic. Use this when the user "
        "asks about something they previously showed or told you "
        "('did you see my dog?', 'what color was that mug?', 'remember "
        "the book?'). The tool returns the most relevant remembered fact, "
        "including a visual description of what was seen at that moment."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "query": types.Schema(
                type=types.Type.STRING,
                description=(
                    "A short topic to recall, e.g. 'dog', 'mug', 'book', "
                    "'Lily'. The tool will find the best matching memory."
                ),
            ),
        },
        required=["query"],
    ),
)])


SET_EMOTION_TOOL = types.Tool(function_declarations=[types.FunctionDeclaration(
    name="set_emotion",
    description=(
        "Set your facial expression for THIS reply. Pick the emotion that "
        "best matches what you're about to say. Call this BEFORE finishing "
        "your reply so the avatar animates correctly while you speak."
    ),
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "emotion": types.Schema(
                type=types.Type.STRING,
                description=(
                    "One of: neutral | happy | amaze | curious | "
                    "sad | confused | sleepy | thinking"
                ),
            ),
        },
        required=["emotion"],
    ),
)])


async def caption_image(client, image_jpeg: bytes) -> str:
    """Ask Gemini Flash for a single-sentence visual description of an image.
    Used to enrich remember() facts so the saved memory carries visual context
    even after the actual image is forgotten."""
    try:
        resp = await client.aio.models.generate_content(
            model=LLM_MODEL,
            contents=[types.Content(role="user", parts=[
                types.Part(text=(
                    "In one short sentence, describe the visible scene "
                    "in this image. Mention colors, prominent objects, "
                    "and the human if visible. No preamble. Output ENGLISH."
                )),
                types.Part(inline_data=types.Blob(
                    data=image_jpeg, mime_type="image/jpeg")),
            ])],
        )
        return _extract_text(resp.candidates[0].content.parts).strip()
    except Exception as e:
        log.warning("caption_image failed: %s", e)
        return ""


class Brain:
    def __init__(self,
                 prompt_template: str,
                 on_remember: Callable[[str, Optional[bytes]], Awaitable[Optional[str]]],
                 on_recall_visual: Optional[Callable[[str], Awaitable[Optional[dict]]]] = None,
                 api_key: Optional[str] = None) -> None:
        self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self._template = prompt_template
        self._on_remember = on_remember
        self._on_recall_visual = on_recall_visual
        # Frame attached to the current turn — passed to remember() so the
        # memory keeps a snapshot of what was seen at that moment.
        self._current_image: Optional[bytes] = None
        # Emotion the model called set_emotion with for the most recent turn.
        # Cleared at the start of each respond(); read back by the server.
        self.last_emotion: str = "neutral"

    def set_template(self, prompt_template: str) -> None:
        """Hot-swap the persona prompt (used when switching characters)."""
        self._template = prompt_template

    @property
    def client(self):
        """Expose the underlying genai client so callers (rocky.py's
        on_remember closure) can request an image caption alongside saving."""
        return self._client

    async def respond(self,
                      transcript: str,
                      image_jpeg: Optional[bytes],
                      memories: list[str],
                      conversation: list[dict],
                      patterns: str = "",
                      adapted_knowledge: str = "",
                      inline_recall: list[str] = None) -> str:
        """One conversational turn. Returns Rocky's reply text.

        memories: list of fact strings, most recent last.
        conversation: list of {user, assistant, ts} dicts.
        patterns: rendered USER PATTERNS block (adaptive style hints).
        adapted_knowledge: rendered ADAPTED KNOWLEDGE block from Adaption Labs.
        """
        self._current_image = image_jpeg
        prompt = self._template.format(
            memories="\n".join(f"- {m}" for m in memories) or "(none yet)",
            conversation=_format_conversation(conversation),
            patterns=patterns or "(none yet)",
            adapted_knowledge=adapted_knowledge or "(none yet)",
        )

        # Build the user turn: ACTIVE MEMORY (full fact list) + optional
        # relevant-recap + text + image. The memory block is injected with
        # every user message so Gemini can't quietly ignore it the way it
        # sometimes does with long system prompts.
        parts: list[types.Part] = []
        if memories:
            memory_block = (
                "ACTIVE MEMORY — facts about this person you already know. "
                "Use them aggressively in your reply:\n"
                + "\n".join(f"  - {m}" for m in memories)
            )
            parts.append(types.Part(text=memory_block))
        if inline_recall:
            recap = "(Especially relevant right now: " + "; ".join(inline_recall) + ")"
            parts.append(types.Part(text=recap))
        parts.append(types.Part(text=transcript))
        if image_jpeg:
            parts.append(types.Part(inline_data=types.Blob(
                data=image_jpeg, mime_type="image/jpeg",
            )))

        contents: list[types.Content] = [types.Content(role="user", parts=parts)]

        # Tool-call loop. The model may call remember() one or more times
        # before producing a final text reply. We cap at 4 iterations to
        # avoid infinite loops.
        # Reset the emotion for this turn; set_emotion tool will update it.
        self.last_emotion = "neutral"

        # Pick which tools are available this turn. Always include remember;
        # include recall_visual only if a callback is wired. (set_emotion is
        # no longer exposed — the UI uses the sphere viz, not an avatar.)
        tools = [REMEMBER_TOOL]
        if self._on_recall_visual is not None:
            tools.append(RECALL_VISUAL_TOOL)

        for _ in range(2):  # was 4; one remember/recall + one final reply is enough
            resp = await self._client.aio.models.generate_content(
                model=LLM_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    tools=tools,
                ),
            )
            cand = resp.candidates[0]
            tool_call_parts = [
                p for p in cand.content.parts
                if getattr(p, "function_call", None)
            ]
            if not tool_call_parts:
                # No tool calls — model produced its final reply.
                return _extract_text(cand.content.parts)

            # Append the model's tool-call turn to history
            contents.append(cand.content)

            # Execute each tool call, build the function_response turn
            tool_response_parts: list[types.Part] = []
            for p in tool_call_parts:
                fc = p.function_call
                if fc.name == "remember":
                    fact = (fc.args or {}).get("fact", "")
                    mid = await self._on_remember(fact, self._current_image)
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name="remember",
                            response={"id": mid or "duplicate"},
                        ),
                    ))
                elif fc.name == "set_emotion":
                    emotion = (fc.args or {}).get("emotion", "neutral")
                    valid = {"neutral", "happy", "amaze", "curious",
                             "sad", "confused", "sleepy", "thinking"}
                    self.last_emotion = emotion if emotion in valid else "neutral"
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name="set_emotion",
                            response={"ok": True, "emotion": self.last_emotion},
                        ),
                    ))
                elif fc.name == "recall_visual" and self._on_recall_visual:
                    query = (fc.args or {}).get("query", "")
                    result = await self._on_recall_visual(query)
                    if result:
                        tool_response_parts.append(types.Part(
                            function_response=types.FunctionResponse(
                                name="recall_visual",
                                response={
                                    "found": True,
                                    "fact": result.get("fact", ""),
                                    "saved_at": result.get("saved_at"),
                                },
                            ),
                        ))
                    else:
                        tool_response_parts.append(types.Part(
                            function_response=types.FunctionResponse(
                                name="recall_visual",
                                response={"found": False},
                            ),
                        ))
                else:
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"error": "unknown tool"},
                        ),
                    ))

            contents.append(types.Content(role="user", parts=tool_response_parts))

        # Hit iteration cap; fall back gracefully.
        log.warning("tool-call loop exceeded cap; returning last partial text")
        return "Let me think about that. Question?"

    async def respond_audio(self,
                            audio_bytes: bytes,
                            audio_mime: str,
                            image_jpeg: Optional[bytes],
                            memories: list[str],
                            conversation: list[dict],
                            patterns: str = "",
                            adapted_knowledge: str = "",
                            inline_recall: list[str] = None) -> Tuple[str, str]:
        """Audio-native turn. Skips a separate STT call: the model hears the
        user's audio directly and emits both the transcript and the reply in
        one multimodal response.

        Returns (user_said, reply). user_said may be empty if the model
        produced an unparseable reply — callers should fall back to the text
        path when that happens.
        """
        self._current_image = image_jpeg
        prompt = self._template.format(
            memories="\n".join(f"- {m}" for m in memories) or "(none yet)",
            conversation=_format_conversation(conversation),
            patterns=patterns or "(none yet)",
            adapted_knowledge=adapted_knowledge or "(none yet)",
        )
        # Append the format directive so the model emits [YOU_SAID]/[REPLY].
        prompt = prompt + AUDIO_FORMAT_INSTRUCTION

        parts: list[types.Part] = []
        # ACTIVE MEMORY block — injected with EVERY turn, right before the
        # audio. Inline injection bypasses Gemini's tendency to deprioritize
        # the system prompt over time. We pass the full fact list since it's
        # text-only and small.
        if memories:
            memory_block = (
                "ACTIVE MEMORY — facts about this person you already know. "
                "Use them aggressively in your reply:\n"
                + "\n".join(f"  - {m}" for m in memories)
            )
            parts.append(types.Part(text=memory_block))
        if inline_recall:
            recap = "(Especially relevant right now: " + "; ".join(inline_recall) + ")"
            parts.append(types.Part(text=recap))
        # Audio replaces the text transcript as the user's "message".
        parts.append(types.Part(inline_data=types.Blob(
            data=audio_bytes,
            mime_type=audio_mime or "audio/webm",
        )))
        if image_jpeg:
            parts.append(types.Part(inline_data=types.Blob(
                data=image_jpeg, mime_type="image/jpeg",
            )))

        contents: list[types.Content] = [types.Content(role="user", parts=parts)]
        self.last_emotion = "neutral"

        tools = [REMEMBER_TOOL]
        if self._on_recall_visual is not None:
            tools.append(RECALL_VISUAL_TOOL)

        for _ in range(2):
            resp = await self._client.aio.models.generate_content(
                model=LLM_MODEL_AUDIO,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    tools=tools,
                ),
            )
            cand = resp.candidates[0]
            tool_call_parts = [
                p for p in cand.content.parts
                if getattr(p, "function_call", None)
            ]
            if not tool_call_parts:
                raw = _extract_text(cand.content.parts)
                return _parse_audio_reply(raw)

            contents.append(cand.content)
            tool_response_parts: list[types.Part] = []
            for p in tool_call_parts:
                fc = p.function_call
                if fc.name == "remember":
                    fact = (fc.args or {}).get("fact", "")
                    mid = await self._on_remember(fact, self._current_image)
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name="remember",
                            response={"id": mid or "duplicate"},
                        ),
                    ))
                elif fc.name == "recall_visual" and self._on_recall_visual:
                    query = (fc.args or {}).get("query", "")
                    result = await self._on_recall_visual(query)
                    if result:
                        tool_response_parts.append(types.Part(
                            function_response=types.FunctionResponse(
                                name="recall_visual",
                                response={
                                    "found": True,
                                    "fact": result.get("fact", ""),
                                    "saved_at": result.get("saved_at"),
                                },
                            ),
                        ))
                    else:
                        tool_response_parts.append(types.Part(
                            function_response=types.FunctionResponse(
                                name="recall_visual",
                                response={"found": False},
                            ),
                        ))
                else:
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"error": "unknown tool"},
                        ),
                    ))
            contents.append(types.Content(role="user", parts=tool_response_parts))

        log.warning("audio tool-call loop exceeded cap")
        return "", "Let me think about that. Question?"


def _parse_audio_reply(raw: str) -> Tuple[str, str]:
    """Pull (user_said, reply) out of a [YOU_SAID]...[REPLY]... tagged
    response. If the tags are missing, return ('', raw) so the caller can
    decide whether to fall back to the STT path."""
    if not raw:
        return "", ""
    you_match = TRANSCRIPT_TAG_RE.search(raw)
    reply_match = REPLY_TAG_RE.search(raw)
    you_said = you_match.group(1).strip() if you_match else ""
    reply = reply_match.group(1).strip() if reply_match else ""
    if not reply:
        # Tags not honored — strip any tag fragments and use the whole text.
        reply = re.sub(r"\[YOU_SAID\].*?\n", "", raw, count=1, flags=re.S).strip()
    return you_said, reply or "Question?"


def _extract_text(parts) -> str:
    return "".join(p.text or "" for p in parts if getattr(p, "text", None)).strip() \
        or "Question?"


def _format_conversation(turns: list[dict]) -> str:
    if not turns:
        return "(none yet)"
    out = []
    for t in turns:
        out.append(f"User: {t['user']}")
        out.append(f"Rocky: {t['assistant']}")
    return "\n".join(out)
