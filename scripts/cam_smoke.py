"""Run on Pi: capture 5 frames and report motion scores."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from camera import CameraService  # noqa: E402


async def main() -> None:
    cam = CameraService(size=(640, 480))
    await cam.start()
    for i in range(5):
        await asyncio.sleep(1.2)
        jpg = cam.latest_jpeg()
        score = cam.motion_score()
        print(f"frame {i}: {len(jpg) if jpg else 0} bytes, motion={score:.2f}")
        if jpg:
            Path(f"/tmp/rocky_cam_{i}.jpg").write_bytes(jpg)
    await cam.stop()


if __name__ == "__main__":
    asyncio.run(main())
