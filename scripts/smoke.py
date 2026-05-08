"""End-to-end hardware smoke test. Run on the Pi.

Records 3 seconds from the Snowball, plays it back through the HyperX dongle,
captures one camera frame, and prints a summary.
"""
import os
import sys
import time
import wave
from pathlib import Path

import numpy as np
import sounddevice as sd
from picamera2 import Picamera2

MIC_NAME = os.environ.get("ROCKY_MIC_DEVICE", "Snowball")
SPK_NAME = os.environ.get("ROCKY_SPEAKER_DEVICE", "HyperX")
SECS = 3
RATE = 16000  # what Gemini Live wants for input
SPK_RATE = 48000  # HyperX native rate; 48000 / 16000 = 3x upsample


def find_device(substring: str, kind: str) -> int:
    """kind: 'input' or 'output'."""
    for i, d in enumerate(sd.query_devices()):
        if substring not in d["name"]:
            continue
        if kind == "input" and d["max_input_channels"] > 0:
            return i
        if kind == "output" and d["max_output_channels"] > 0:
            return i
    raise ValueError(f"no {kind} device matching {substring!r}")


def record_and_play() -> None:
    mic_idx = find_device(MIC_NAME, "input")
    spk_idx = find_device(SPK_NAME, "output")
    print(f"mic: [{mic_idx}] {sd.query_devices(mic_idx)['name']}")
    print(f"spk: [{spk_idx}] {sd.query_devices(spk_idx)['name']}")

    print(f"recording {SECS}s @ {RATE}Hz mono ... speak now")
    audio = sd.rec(int(SECS * RATE), samplerate=RATE, channels=1,
                   dtype="int16", device=mic_idx)
    sd.wait()
    peak = int(np.max(np.abs(audio)))
    print(f"peak: {peak} ({peak / 32768 * 100:.1f}% of full scale)")
    if peak < 500:
        print("  WARNING: silent or very weak — check Snowball gain switch")

    print(f"playing back ...")
    # Upsample from RATE to SPK_RATE via integer repeat (ratio must be integer).
    ratio = SPK_RATE // RATE
    audio_up = np.repeat(audio, ratio, axis=0)
    sd.play(audio_up, samplerate=SPK_RATE, device=spk_idx)
    sd.wait()
    print("  playback done")


def capture_frame() -> None:
    out = Path("/tmp/rocky_smoke.jpg")
    cam = Picamera2()
    cfg = cam.create_still_configuration(main={"size": (640, 480)})
    cam.configure(cfg)
    cam.start()
    time.sleep(1.5)
    cam.capture_file(str(out))
    cam.stop()
    print(f"camera frame: {out} ({out.stat().st_size} bytes)")


if __name__ == "__main__":
    record_and_play()
    capture_frame()
    print("smoke OK")
