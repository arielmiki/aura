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


class Brain:
    def __init__(self,
                 prompt_template: str,
                 on_remember: Callable[[str], Awaitable[Optional[str]]],
                 api_key: Optional[str] = None) -> None:
        self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self._template = prompt_template
        self._on_remember = on_remember

    async def respond(self,
                      transcript: str,
                      image_jpeg: Optional[bytes],
                      memories: list[str],
                      conversation: list[dict]) -> str:
        """One conversational turn. Returns Rocky's reply text.

        memories: list of fact strings, most recent last.
        conversation: list of {user, assistant, ts} dicts.
        """
        prompt = self._template.format(
            memories="\n".join(f"- {m}" for m in memories) or "(none yet)",
            conversation=_format_conversation(conversation),
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
        for _ in range(4):
            resp = await self._client.aio.models.generate_content(
                model=LLM_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    tools=[REMEMBER_TOOL],
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
                    mid = await self._on_remember(fact)
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name="remember",
                            response={"id": mid or "duplicate"},
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
