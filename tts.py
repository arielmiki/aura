"""ElevenLabs TTS wrapper. Streams MP3 chunks as they arrive."""
from __future__ import annotations

import logging
import os
from typing import AsyncIterator, Optional

from elevenlabs.client import ElevenLabs

log = logging.getLogger(__name__)

TTS_MODEL = "eleven_turbo_v2_5"  # low-latency
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


class TTS:
    def __init__(self,
                 api_key: Optional[str] = None,
                 voice_id: Optional[str] = None) -> None:
        self._client = ElevenLabs(api_key=api_key or os.environ["ELEVENLABS_API_KEY"])
        self._voice_id = voice_id or os.environ["ELEVENLABS_VOICE_ID"]

    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Returns an async iterator of MP3 byte chunks.

        ElevenLabs SDK exposes a synchronous generator; we wrap it for use in
        FastAPI's StreamingResponse via a small adapter.

        Note: In elevenlabs 2.x, voice_id is the first positional argument to
        client.text_to_speech.stream(), not a keyword argument."""
        sync_gen = self._client.text_to_speech.stream(
            self._voice_id,
            text=text,
            model_id=TTS_MODEL,
            output_format=DEFAULT_OUTPUT_FORMAT,
        )
        return _async_iter(sync_gen)


async def _async_iter(sync_gen) -> AsyncIterator[bytes]:
    """Wrap a sync generator as an async one. FastAPI's StreamingResponse
    accepts either, but the rest of our pipeline is async, so we adapt."""
    import asyncio
    loop = asyncio.get_event_loop()
    sentinel = object()

    def next_chunk():
        try:
            return next(sync_gen)
        except StopIteration:
            return sentinel

    while True:
        chunk = await loop.run_in_executor(None, next_chunk)
        if chunk is sentinel:
            return
        if chunk:
            yield chunk
