"""Gemini 2.5 Flash brain with remember(fact) tool dispatch.

Builds the system prompt from persona template + memories + recent conversation,
calls Gemini once, and loops on any tool calls (executing remember and feeding
the response back) until the model produces a text response.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Awaitable, Callable, Optional

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

LLM_MODEL = "gemini-2.5-flash"

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
                      adapted_knowledge: str = "") -> str:
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

        # Build the user turn: text + optional image
        parts: list[types.Part] = [types.Part(text=transcript)]
        if image_jpeg:
            parts.append(types.Part(inline_data=types.Blob(
                data=image_jpeg, mime_type="image/jpeg",
            )))

        contents: list[types.Content] = [types.Content(role="user", parts=parts)]

        # Tool-call loop. The model may call remember() one or more times
        # before producing a final text reply. We cap at 4 iterations to
        # avoid infinite loops.
        # Pick which tools are available this turn. Always include remember;
        # include recall_visual only if rocky.py wired a callback for it.
        tools = [REMEMBER_TOOL]
        if self._on_recall_visual is not None:
            tools.append(RECALL_VISUAL_TOOL)

        for _ in range(4):
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
