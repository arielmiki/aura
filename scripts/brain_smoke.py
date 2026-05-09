"""Run on Pi. Speak; Rocky should reply.

No camera, no vocab, no tools. Just round-trip voice via Gemini Live.
"""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from audio import MicStream, SpeakerStream  # noqa: E402
from brain import GeminiBrain  # noqa: E402


async def main() -> None:
    load_dotenv()
    mic = MicStream(os.environ.get("ROCKY_MIC_DEVICE", "Snowball"))
    spk = SpeakerStream(os.environ.get("ROCKY_SPEAKER_DEVICE", "HyperX"))
    await mic.start()
    await spk.start()

    sysprompt = Path("prompts/rocky_system.md").read_text()

    async def play(audio: bytes) -> None:
        await spk.write(audio)

    async def log_text(t: str) -> None:
        print(f"[rocky text] {t!r}")

    brain = GeminiBrain(sysprompt, on_audio_out=play, on_text_out=log_text)
    await brain.start()

    async def pump():
        async for chunk in mic.chunks():
            await brain.send_audio(chunk)

    pump_task = asyncio.create_task(pump())

    print("Speak. Press Ctrl-C to exit.")
    try:
        await asyncio.Event().wait()  # forever
    except KeyboardInterrupt:
        pass
    finally:
        pump_task.cancel()
        await brain.stop()
        await mic.stop()
        await spk.stop()


if __name__ == "__main__":
    asyncio.run(main())
