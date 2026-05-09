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

LIVE_MODEL = "gemini-2.5-flash-live-preview"  # verify at smoke time


class GeminiBrain:
    def __init__(self,
                 system_prompt: str,
                 on_audio_out: Callable[[bytes], Awaitable[None]],
                 on_text_out: Callable[[str], Awaitable[None]] | None = None,
                 voice: str = "Puck") -> None:
        api_key = os.environ["GEMINI_API_KEY"]
        self._client = genai.Client(api_key=api_key)
        self._on_audio_out = on_audio_out
        self._on_text_out = on_text_out
        self._system_prompt = system_prompt
        self._voice = voice
        self._session = None
        self._send_queue: asyncio.Queue = asyncio.Queue()
        self._tasks: list[asyncio.Task] = []

    async def start(self) -> None:
        config = types.LiveConnectConfig(
            response_modalities=[types.Modality.AUDIO],
            system_instruction=types.Content(parts=[types.Part(text=self._system_prompt)]),
            speech_config=types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self._voice)
                )
            ),
        )
        self._cm = self._client.aio.live.connect(model=LIVE_MODEL, config=config)
        self._session = await self._cm.__aenter__()
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
            try:
                if kind == "audio":
                    await self._session.send_realtime_input(
                        media=types.Blob(data=payload, mime_type="audio/pcm;rate=16000")
                    )
                elif kind == "image":
                    await self._session.send_realtime_input(
                        media=types.Blob(data=payload, mime_type="image/jpeg")
                    )
                elif kind == "text":
                    await self._session.send_client_content(
                        turns=types.Content(parts=[types.Part(text=payload)]),
                        turn_complete=True,
                    )
            except Exception:
                log.exception("send failed")

    async def _receiver(self) -> None:
        try:
            async for resp in self._session.receive():
                # Audio
                if resp.data:
                    await self._on_audio_out(resp.data)
                # Text (used for tool args / debug)
                if resp.text and self._on_text_out:
                    await self._on_text_out(resp.text)
                # Tool calls handled in Task 7
        except Exception:
            log.exception("receive failed")
