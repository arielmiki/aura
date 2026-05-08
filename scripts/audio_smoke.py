"""Run on Pi: capture 3 seconds, then play back at 24 kHz (Gemini's rate).
Confirms both classes work end-to-end."""
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import numpy as np  # noqa: E402

from audio import MicStream, SpeakerStream, GEMINI_OUT_RATE  # noqa: E402


async def main() -> None:
    mic = MicStream(os.environ.get("ROCKY_MIC_DEVICE", "Snowball"))
    spk = SpeakerStream(os.environ.get("ROCKY_SPEAKER_DEVICE", "HyperX"))
    await mic.start()
    await spk.start()

    print("speak for 3 seconds...")
    pcm_chunks = []
    async def collect():
        async for chunk in mic.chunks():
            pcm_chunks.append(chunk)
    task = asyncio.create_task(collect())
    await asyncio.sleep(3.0)
    task.cancel()

    raw = b"".join(pcm_chunks)
    arr = np.frombuffer(raw, dtype=np.int16)
    print(f"captured {len(arr)} samples, peak={int(np.max(np.abs(arr)))}")

    # SpeakerStream.write expects 24 kHz mono PCM. Resample 16k -> 24k.
    stretched = np.interp(
        np.linspace(0, len(arr), int(len(arr) * GEMINI_OUT_RATE / 16000)),
        np.arange(len(arr)),
        arr,
    ).astype(np.int16)
    await spk.write(stretched.tobytes())
    await asyncio.sleep(len(stretched) / GEMINI_OUT_RATE + 0.5)

    await mic.stop()
    await spk.stop()
    print("audio smoke OK")


if __name__ == "__main__":
    asyncio.run(main())
