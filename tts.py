"""ElevenLabs TTS wrapper. Streams MP3 chunks as they arrive.

Uses the multilingual eleven_v3 model so Rocky speaks naturally in
English and Indonesian (and 70+ other languages). A tiny heuristic
detects Indonesian text and sets the model's `language_code` hint
accordingly; otherwise the model auto-detects.
"""
from __future__ import annotations

import logging
import os
import re
from typing import AsyncIterator, Optional

from elevenlabs.client import ElevenLabs

log = logging.getLogger(__name__)

TTS_MODEL = "eleven_flash_v2_5"  # fastest multilingual; lower latency than v3
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"

# A short list of high-frequency Indonesian function words. If any appear,
# treat the reply as Indonesian. This catches the common case where Rocky
# echoes the user's language.
INDONESIAN_HINTS = re.compile(
    r"\b("
    r"saya|aku|kamu|kita|kami|dia|mereka|"
    r"yang|tidak|nggak|gak|bukan|"
    r"dan|atau|tapi|dari|untuk|dengan|pada|"
    r"sudah|akan|bisa|mau|harus|"
    r"ini|itu|apa|siapa|kenapa|bagaimana|"
    r"baik|bagus|jelek|"
    r"ya|iya|terima\s+kasih|"
    r"halo|hai|selamat|nama|senang|bertemu|"
    r"makan|minum|tidur|jalan|"
    r"pagi|siang|sore|malam"
    r")\b",
    re.IGNORECASE,
)


def detect_language(text: str) -> str:
    """Returns 'id' (Indonesian) or 'en' (English fallback)."""
    return "id" if INDONESIAN_HINTS.search(text or "") else "en"


class TTS:
    def __init__(self,
                 api_key: Optional[str] = None,
                 voice_id: Optional[str] = None) -> None:
        self._client = ElevenLabs(api_key=api_key or os.environ["ELEVENLABS_API_KEY"])
        self._voice_id = voice_id or os.environ["ELEVENLABS_VOICE_ID"]

    def stream(self, text: str, language_code: Optional[str] = None) -> AsyncIterator[bytes]:
        """Returns an async iterator of MP3 byte chunks.

        If `language_code` is None, we auto-detect (en|id) from the text.
        """
        lang = language_code or detect_language(text)
        log.info("tts: lang=%s len=%d", lang, len(text))
        sync_gen = self._client.text_to_speech.stream(
            self._voice_id,
            text=text,
            model_id=TTS_MODEL,
            output_format=DEFAULT_OUTPUT_FORMAT,
            language_code=lang,
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
