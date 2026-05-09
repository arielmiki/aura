"""Pi-Rocky entry point. Mic → Gemini Live (image + tools) → speaker, plus web UI."""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

import numpy as np
from dotenv import load_dotenv

from audio import MicStream, SpeakerStream
from brain import GeminiBrain
from camera import CameraService
from idle import IdleGate, run_idle_loop
from vocab import VocabStore
from web.server import make_app, serve

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("rocky")

SEED_VOCAB = ["YES", "NO", "HUMAN", "ROCKY", "QUESTION", "NEW", "WORD",
              "HELLO", "GOOD", "BAD", "BIG", "SMALL"]


class Status:
    def __init__(self) -> None:
        self.value = "idle"

    def set(self, store: VocabStore, value: str) -> None:
        if self.value == value:
            return
        self.value = value
        store.set_status(value)


async def amain() -> None:
    load_dotenv()
    port = int(os.environ.get("ROCKY_WEB_PORT", "8000"))
    mic = MicStream(os.environ.get("ROCKY_MIC_DEVICE", "Snowball"))
    spk = SpeakerStream(os.environ.get("ROCKY_SPEAKER_DEVICE", "HyperX"))
    cam = CameraService(size=(640, 480))
    vocab = VocabStore(path=Path("vocab.json"), seed=SEED_VOCAB)
    status = Status()

    await mic.start()
    await spk.start()
    await cam.start()

    template = Path("prompts/rocky_system.md").read_text()

    async def play(audio: bytes) -> None:
        status.set(vocab, "speaking")
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

    gate = IdleGate(silence_secs=30.0, motion_threshold=5.0, cooldown_secs=60.0)
    idle_task = asyncio.create_task(
        run_idle_loop(gate, cam, brain, get_silent_for=lambda: 0.0)
    )

    app = make_app(vocab, cam, lambda: status.value)
    web_task = asyncio.create_task(serve(app, port=port))

    async def pump_mic() -> None:
        last_frame_time = 0.0
        async for chunk in mic.chunks():
            status.set(vocab, "speaking" if spk.is_speaking else "listening")
            await brain.send_audio(chunk)
            arr = np.frombuffer(chunk, dtype=np.int16)
            if int(np.max(np.abs(arr))) > 800:
                gate.note_user_audio()
            now = asyncio.get_event_loop().time()
            jpg = cam.latest_jpeg()
            if jpg and now - last_frame_time > 1.0:
                await brain.send_image_jpeg(jpg)
                last_frame_time = now

    log.info("rocky online at http://pibot:%d (vocab: %s)", port, vocab.words())
    try:
        await pump_mic()
    finally:
        idle_task.cancel()
        web_task.cancel()
        await brain.stop()
        await cam.stop()
        await mic.stop()
        await spk.stop()


if __name__ == "__main__":
    try:
        asyncio.run(amain())
    except KeyboardInterrupt:
        pass
