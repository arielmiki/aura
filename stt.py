"""ElevenLabs Scribe STT wrapper."""
from __future__ import annotations

import io
import logging
import os
from typing import Optional

from elevenlabs.client import ElevenLabs

log = logging.getLogger(__name__)

SCRIBE_MODEL = "scribe_v1"


class STT:
    def __init__(self, api_key: Optional[str] = None) -> None:
        self._client = ElevenLabs(api_key=api_key or os.environ["ELEVENLABS_API_KEY"])

    def transcribe(self, audio_bytes: bytes) -> str:
        """Synchronous; ElevenLabs SDK is sync here. Caller wraps in to_thread
        if it needs to stay non-blocking."""
        result = self._client.speech_to_text.convert(
            file=io.BytesIO(audio_bytes),
            model_id=SCRIBE_MODEL,
        )
        return (result.text or "").strip()
