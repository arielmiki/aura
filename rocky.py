"""Pi-Rocky entry point. Mic → Gemini Live (with image + tools) → speaker.
The vocab store + camera service are wired in here. Web UI added in Task 8.
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from dotenv import load_dotenv

from audio import MicStream, SpeakerStream
from brain import GeminiBrain
from camera import CameraService
from vocab import VocabStore

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rocky")

SEED_VOCAB = ["YES", "NO", "HUMAN", "ROCKY", "QUESTION", "NEW", "WORD",
              "HELLO", "GOOD", "BAD", "BIG", "SMALL"]


async def amain() -> None:
    load_dotenv()
    mic = MicStream(os.environ.get("ROCKY_MIC_DEVICE", "Snowball"))
    spk = SpeakerStream(os.environ.get("ROCKY_SPEAKER_DEVICE", "HyperX"))
    cam = CameraService(size=(640, 480))
    vocab = VocabStore(path=Path("vocab.json"), seed=SEED_VOCAB)

    await mic.start()
    await spk.start()
    await cam.start()

    template = Path("prompts/rocky_system.md").read_text()

    async def play(audio: bytes) -> None:
        await spk.write(audio)

    async def learn_word(word: str, description: str) -> None:
        added = vocab.learn_word(word, description)
        log.info("learn_word(%s) -> added=%s", word, added)

    brain = GeminiBrain(
        system_prompt_template=template,
        vocab_provider=vocab.words,
        learn_word_handler=learn_word,
        on_audio_out=play,
    )
    await brain.start()

    async def pump_mic() -> None:
        # Send current camera frame at most once per second alongside audio.
        last_frame_time = 0.0
        async for chunk in mic.chunks():
            await brain.send_audio(chunk)
            now = asyncio.get_event_loop().time()
            jpg = cam.latest_jpeg()
            if jpg and now - last_frame_time > 1.0:
                await brain.send_image_jpeg(jpg)
                last_frame_time = now

    log.info("rocky online — vocab: %s", vocab.words())
    try:
        await pump_mic()
    finally:
        await brain.stop()
        await cam.stop()
        await mic.stop()
        await spk.stop()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
