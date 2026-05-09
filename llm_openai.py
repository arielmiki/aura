"""OpenAI brain — drop-in alternative to llm.Brain.

Same public interface as Brain: respond(transcript, image, ...) for the
text path, respond_audio(...) as a no-op (returns empty so the caller
falls back to its STT pipeline). Tool calls (remember, recall_visual)
are mapped to OpenAI function-calling.

To switch backends, set BRAIN_BACKEND=openai (default: gemini). To pin
a specific model, set OPENAI_MODEL (default: "gpt-5.5"). If the model
ID doesn't exist on your account you'll get a 404 — override with a
valid one via OPENAI_MODEL.
"""
from __future__ import annotations

import base64
import json
import logging
import os
from typing import Awaitable, Callable, Optional, Tuple

from openai import AsyncOpenAI

log = logging.getLogger(__name__)

OPENAI_MODEL = os.environ.get("OPENAI_MODEL", "gpt-5.5")

REMEMBER_TOOL = {
    "type": "function",
    "function": {
        "name": "remember",
        "description": "Save a fact about the user to long-term memory.",
        "parameters": {
            "type": "object",
            "properties": {
                "fact": {
                    "type": "string",
                    "description": "A one-sentence fact about the user, e.g. 'User has a corgi named Lily.'",
                },
            },
            "required": ["fact"],
        },
    },
}

RECALL_VISUAL_TOOL = {
    "type": "function",
    "function": {
        "name": "recall_visual",
        "description": (
            "Retrieve a past visual memory by topic. Use this when the user "
            "asks about something they previously showed or told you "
            "('did you see my dog?', 'what color was that mug?')."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "A short topic to recall, e.g. 'dog', 'mug', 'book'.",
                },
            },
            "required": ["query"],
        },
    },
}


class BrainOpenAI:
    def __init__(self,
                 prompt_template: str,
                 on_remember: Callable[[str, Optional[bytes]], Awaitable[Optional[str]]],
                 on_recall_visual: Optional[Callable[[str], Awaitable[Optional[dict]]]] = None,
                 api_key: Optional[str] = None) -> None:
        self._client = AsyncOpenAI(api_key=api_key or os.environ["OPENAI_API_KEY"])
        self._template = prompt_template
        self._on_remember = on_remember
        self._on_recall_visual = on_recall_visual
        self._current_image: Optional[bytes] = None
        self.last_emotion: str = "neutral"

    def set_template(self, prompt_template: str) -> None:
        self._template = prompt_template

    @property
    def client(self):
        # Mirrors Brain.client — currently unused by OpenAI path but kept
        # for interface parity in case rocky.py reaches in.
        return self._client

    async def respond(self,
                      transcript: str,
                      image_jpeg: Optional[bytes],
                      memories: list[str],
                      conversation: list[dict],
                      patterns: str = "",
                      adapted_knowledge: str = "",
                      inline_recall: list[str] = None) -> str:
        self._current_image = image_jpeg
        system_prompt = self._template.format(
            memories="\n".join(f"- {m}" for m in memories) or "(none yet)",
            conversation=_format_conversation(conversation),
            patterns=patterns or "(none yet)",
            adapted_knowledge=adapted_knowledge or "(none yet)",
        )

        # Build the user message: ACTIVE MEMORY (forces model to look) +
        # optional relevant recap + transcript + image.
        user_blocks: list[dict] = []
        if memories:
            mem_text = (
                "ACTIVE MEMORY — facts about this person you already know. "
                "Use them aggressively in your reply:\n"
                + "\n".join(f"  - {m}" for m in memories)
            )
            user_blocks.append({"type": "text", "text": mem_text})
        if inline_recall:
            user_blocks.append({
                "type": "text",
                "text": "(Especially relevant right now: " + "; ".join(inline_recall) + ")",
            })
        user_blocks.append({"type": "text", "text": transcript})
        if image_jpeg:
            b64 = base64.b64encode(image_jpeg).decode("ascii")
            user_blocks.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_blocks},
        ]

        tools = [REMEMBER_TOOL]
        if self._on_recall_visual is not None:
            tools.append(RECALL_VISUAL_TOOL)

        self.last_emotion = "neutral"

        # Tool-call loop: cap at 2 (one tool round-trip + final reply is enough).
        for _ in range(2):
            resp = await self._client.chat.completions.create(
                model=OPENAI_MODEL,
                messages=messages,
                tools=tools,
            )
            msg = resp.choices[0].message
            tool_calls = getattr(msg, "tool_calls", None) or []

            if not tool_calls:
                return (msg.content or "Question?").strip()

            # Append the assistant turn (tool calls) to history
            messages.append({
                "role": "assistant",
                "content": msg.content,
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {
                            "name": tc.function.name,
                            "arguments": tc.function.arguments,
                        },
                    } for tc in tool_calls
                ],
            })

            # Execute each tool call, append a tool response message
            for tc in tool_calls:
                name = tc.function.name
                try:
                    args = json.loads(tc.function.arguments or "{}")
                except json.JSONDecodeError:
                    args = {}

                if name == "remember":
                    fact = args.get("fact", "")
                    mid = await self._on_remember(fact, self._current_image)
                    result = {"id": mid or "duplicate"}
                elif name == "recall_visual" and self._on_recall_visual:
                    query = args.get("query", "")
                    found = await self._on_recall_visual(query)
                    result = ({"found": True, **found}
                              if found else {"found": False})
                else:
                    result = {"error": "unknown tool"}

                messages.append({
                    "role": "tool",
                    "tool_call_id": tc.id,
                    "name": name,
                    "content": json.dumps(result),
                })

        log.warning("openai tool-call loop exceeded cap")
        return "Let me think about that. Question?"

    async def respond_audio(self,
                            audio_bytes: bytes,
                            audio_mime: str,
                            image_jpeg: Optional[bytes],
                            memories: list[str],
                            conversation: list[dict],
                            patterns: str = "",
                            adapted_knowledge: str = "",
                            inline_recall: list[str] = None
                            ) -> Tuple[str, str]:
        """OpenAI text-completion models don't accept raw audio in the chat
        API. Returning empty strings signals the server's /turn handler to
        fall back to its STT path (ElevenLabs Scribe → respond())."""
        return "", ""


def _format_conversation(turns: list[dict]) -> str:
    if not turns:
        return "(none yet)"
    out = []
    for t in turns:
        out.append(f"User: {t['user']}")
        out.append(f"Assistant: {t['assistant']}")
    return "\n".join(out)
