"""Smoke test: synthesize a sentence in Rocky's voice and play it back."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from tts import TTS  # noqa: E402


async def main() -> None:
    load_dotenv()
    text = " ".join(sys.argv[1:]) or "Hello. Rocky here. Question?"
    print(f"synthesizing: {text!r}")

    out_path = Path("/tmp/rocky_tts.mp3")
    with out_path.open("wb") as f:
        async for chunk in TTS().stream(text):
            f.write(chunk)
    size = out_path.stat().st_size
    print(f"wrote {size} bytes to {out_path}")

    # macOS: use afplay to play
    if sys.platform == "darwin":
        subprocess.run(["afplay", str(out_path)])


if __name__ == "__main__":
    asyncio.run(main())
