"""Pi-Rocky entry point.

Wires mic capture → Gemini Live → speaker playback. Camera, vocab, idle, and
web UI are wired in by later tasks.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from audio import MicStream, SpeakerStream
from brain import GeminiBrain

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rocky")


async def amain() -> None:
    load_dotenv()
    mic = MicStream(os.environ.get("ROCKY_MIC_DEVICE", "Snowball"))
    spk = SpeakerStream(os.environ.get("ROCKY_SPEAKER_DEVICE", "HyperX"))
    await mic.start()
    await spk.start()

    sysprompt = Path("prompts/rocky_system.md").read_text()

    async def play(audio: bytes) -> None:
        await spk.write(audio)

    brain = GeminiBrain(sysprompt, on_audio_out=play)
    await brain.start()

    async def pump_mic() -> None:
        async for chunk in mic.chunks():
            await brain.send_audio(chunk)

    log.info("rocky online — speak to it")
    try:
        await pump_mic()
    finally:
        await brain.stop()
        await mic.stop()
        await spk.stop()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
