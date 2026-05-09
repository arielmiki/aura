"""ElevenLabs Scribe STT wrapper.

Scribe only accepts a SINGLE language_code per call (no multi-language
hint), so we pin it to English by default — that's the primary use case
and it dramatically reduces hallucinated Polish/Spanish/etc. transcripts
when the room has background chatter. Override via ROCKY_STT_LANG=id
(or any ISO 639-1 code) if you want a different default.
"""
from __future__ import annotations

import io
import logging
import os
from typing import Optional

from elevenlabs.client import ElevenLabs

log = logging.getLogger(__name__)

SCRIBE_MODEL = "scribe_v1"
DEFAULT_LANGUAGE = os.environ.get("ROCKY_STT_LANG", "en")


class STT:
    def __init__(self,
                 api_key: Optional[str] = None,
                 language_code: Optional[str] = None) -> None:
        self._client = ElevenLabs(api_key=api_key or os.environ["ELEVENLABS_API_KEY"])
        self._language_code = language_code or DEFAULT_LANGUAGE
        log.info("STT pinned to language_code=%r", self._language_code)

    def transcribe(self, audio_bytes: bytes) -> str:
        """Synchronous; ElevenLabs SDK is sync here. Caller wraps in to_thread
        if it needs to stay non-blocking."""
        result = self._client.speech_to_text.convert(
            file=io.BytesIO(audio_bytes),
            model_id=SCRIBE_MODEL,
            language_code=self._language_code,
            num_speakers=1,
            diarize=False,
            tag_audio_events=False,
            temperature=0.0,
        )
        return (result.text or "").strip()
