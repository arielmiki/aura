"""Gemini Live session manager.

Streams mic PCM in, image bytes in (optional, added later), text and audio out.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Awaitable, Callable

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

LIVE_MODEL = "gemini-3.1-flash-live-preview"


class GeminiBrain:
    def __init__(self,
                 system_prompt_template: str,
                 vocab_provider,                       # callable -> list[str]
                 learn_word_handler,                   # async (word, desc) -> None
                 on_audio_out: Callable[[bytes], Awaitable[None]],
                 on_text_out: Callable[[str], Awaitable[None]] | None = None,
                 voice: str = "Puck") -> None:
        api_key = os.environ["GEMINI_API_KEY"]
        self._client = genai.Client(api_key=api_key)
        self._on_audio_out = on_audio_out
        self._on_text_out = on_text_out
        self._template = system_prompt_template
        self._vocab_provider = vocab_provider
        self._learn_word_handler = learn_word_handler
        self._voice = voice
        self._session = None
        self._send_queue: asyncio.Queue = asyncio.Queue(maxsize=200)
        self._tasks: list[asyncio.Task] = []
        self._alive = False

    async def start(self) -> None:
        prompt = self._template.format(vocab=", ".join(self._vocab_provider()))
        learn_tool = types.Tool(function_declarations=[types.FunctionDeclaration(
            name="learn_word",
            description="Add a word to Rocky's vocabulary after the human teaches it.",
            parameters=types.Schema(
                type=types.Type.OBJECT,
                properties={
                    "word": types.Schema(type=types.Type.STRING,
                                         description="The new word, e.g. 'PEN'."),
                    "description": types.Schema(type=types.Type.STRING,
                                                description="One sentence about what was shown."),
                },
                required=["word", "description"],
            ),
        )])
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=types.Content(parts=[types.Part(text=prompt)]),
            tools=[learn_tool],
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice)
                )
            ),
        )
        self._cm = self._client.aio.live.connect(model=LIVE_MODEL, config=config)
        self._session = await self._cm.__aenter__()
        self._alive = True
        self._tasks.append(asyncio.create_task(self._sender()))
        self._tasks.append(asyncio.create_task(self._receiver()))
        log.info("gemini live session started")

    async def stop(self) -> None:
        for t in self._tasks:
            t.cancel()
        if self._session:
            await self._cm.__aexit__(None, None, None)

    async def send_audio(self, pcm16k_mono: bytes) -> None:
        await self._send_queue.put(("audio", pcm16k_mono))

    async def send_image_jpeg(self, jpeg: bytes) -> None:
        await self._send_queue.put(("image", jpeg))

    async def send_text(self, text: str) -> None:
        await self._send_queue.put(("text", text))

    async def _sender(self) -> None:
        while True:
            kind, payload = await self._send_queue.get()
            if not self._alive:
                continue  # drop quietly while session is dead
            try:
                if kind == "audio":
                    await self._session.send_realtime_input(
                        audio=types.Blob(data=payload, mime_type="audio/pcm;rate=16000")
                    )
                elif kind == "image":
                    await self._session.send_realtime_input(
                        video=types.Blob(data=payload, mime_type="image/jpeg")
                    )
                elif kind == "text":
                    await self._session.send_client_content(
                        turns=types.Content(parts=[types.Part(text=payload)]),
                        turn_complete=True,
                    )
            except Exception as e:
                if self._alive:
                    log.warning("send failed (%s) — marking session dead", type(e).__name__)
                    self._alive = False

    async def _receiver(self) -> None:
        try:
            async for resp in self._session.receive():
                if not self._alive:
                    break
                if resp.data:
                    await self._on_audio_out(resp.data)
                if resp.text and self._on_text_out:
                    await self._on_text_out(resp.text)
                tool_call = getattr(resp, "tool_call", None)
                if tool_call:
                    for fc in tool_call.function_calls:
                        if fc.name == "learn_word":
                            args = fc.args or {}
                            await self._learn_word_handler(
                                args.get("word", ""), args.get("description", "")
                            )
                            await self._session.send_tool_response(
                                function_responses=[types.FunctionResponse(
                                    id=fc.id, name="learn_word",
                                    response={"ok": True},
                                )]
                            )
        except Exception as e:
            if self._alive:
                log.warning("receive failed (%s) — marking session dead", type(e).__name__)
                self._alive = False
