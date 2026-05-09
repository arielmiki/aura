"""Smoke test: take a wav file, send to ElevenLabs Scribe, print transcript.

Usage:
  python scripts/stt_smoke.py /tmp/sample.wav
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from stt import STT  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python scripts/stt_smoke.py <wav-or-mp3-path>")
        sys.exit(1)
    load_dotenv()
    audio = Path(sys.argv[1]).read_bytes()
    print(f"sending {len(audio)} bytes to ElevenLabs Scribe...")
    transcript = STT().transcribe(audio)
    print(f"transcript: {transcript!r}")


if __name__ == "__main__":
    main()
