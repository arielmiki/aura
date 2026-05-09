"""Camera service: keeps the latest JPEG frame in memory and computes a
crude motion score by comparing successive frames.

Auto-detects backend at construction:
  - picamera2 (Pi)  — preferred when importable
  - cv2 (Mac/PC)    — fallback using cv2.VideoCapture(0)

Override with ROCKY_CAMERA_BACKEND=picamera2 | cv2 | auto.
"""
from __future__ import annotations

import asyncio
import io
import logging
import os
from typing import Optional

import numpy as np
from PIL import Image

log = logging.getLogger(__name__)

BACKEND_ENV = os.environ.get("ROCKY_CAMERA_BACKEND", "auto")


class _Picamera2Backend:
    def __init__(self, size, hflip, vflip):
        from picamera2 import Picamera2
        from libcamera import Transform
        self._cam = Picamera2()
        cfg = self._cam.create_still_configuration(
            main={"size": size, "format": "RGB888"},
            transform=Transform(hflip=hflip, vflip=vflip),
        )
        self._cam.configure(cfg)

    def start(self): self._cam.start()
    def stop(self): self._cam.stop()
    def capture_array(self): return self._cam.capture_array()


class _Cv2Backend:
    def __init__(self, size, hflip, vflip):
        import cv2
        self._cv2 = cv2
        self._size = size
        self._hflip = hflip
        self._vflip = vflip
        self._cap: "cv2.VideoCapture | None" = None

    def start(self):
        cap = self._cv2.VideoCapture(0)
        if not cap.isOpened():
            raise RuntimeError("cv2.VideoCapture(0) could not open default webcam")
        cap.set(self._cv2.CAP_PROP_FRAME_WIDTH, self._size[0])
        cap.set(self._cv2.CAP_PROP_FRAME_HEIGHT, self._size[1])
        self._cap = cap

    def stop(self):
        if self._cap:
            self._cap.release()

    def capture_array(self) -> np.ndarray:
        assert self._cap is not None
        ok, frame_bgr = self._cap.read()
        if not ok or frame_bgr is None:
            raise RuntimeError("webcam read failed")
        rgb = self._cv2.cvtColor(frame_bgr, self._cv2.COLOR_BGR2RGB)
        if self._hflip:
            rgb = self._cv2.flip(rgb, 1)
        if self._vflip:
            rgb = self._cv2.flip(rgb, 0)
        return rgb


def _select_backend_class():
    if BACKEND_ENV == "picamera2":
        return _Picamera2Backend
    if BACKEND_ENV == "cv2":
        return _Cv2Backend
    # auto
    try:
        import picamera2  # noqa: F401
        return _Picamera2Backend
    except ImportError:
        return _Cv2Backend


class CameraService:
    def __init__(self, size: tuple[int, int] = (640, 480),
                 hflip: bool = False, vflip: bool = False,
                 fps: float = 1.0) -> None:
        backend_cls = _select_backend_class()
        log.info("camera backend: %s", backend_cls.__name__)
        self._backend = backend_cls(size, hflip, vflip)
        self._fps = fps
        self._latest_jpeg: Optional[bytes] = None
        self._latest_array: Optional[np.ndarray] = None
        self._prev_array: Optional[np.ndarray] = None
        self._task: Optional[asyncio.Task] = None
        self._motion_score = 0.0

    async def start(self) -> None:
        self._backend.start()
        await asyncio.sleep(1.0)  # let AE/AWB settle
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self._backend.stop()

    def latest_jpeg(self) -> Optional[bytes]:
        return self._latest_jpeg

    def motion_score(self) -> float:
        return self._motion_score

    def _capture_and_encode(self) -> tuple[np.ndarray, bytes, float]:
        """Blocking work — runs in a worker thread so it doesn't starve the loop."""
        arr = self._backend.capture_array()
        buf = io.BytesIO()
        Image.fromarray(arr).save(buf, format="JPEG", quality=70)
        motion = 0.0
        if self._prev_array is not None and self._prev_array.shape == arr.shape:
            diff = np.abs(arr.astype(np.int16) - self._prev_array.astype(np.int16))
            motion = float(diff.mean())
        return arr, buf.getvalue(), motion

    async def _loop(self) -> None:
        period = 1.0 / self._fps
        while True:
            try:
                arr, jpg, motion = await asyncio.to_thread(self._capture_and_encode)
                self._latest_jpeg = jpg
                self._motion_score = motion
                self._prev_array = self._latest_array
                self._latest_array = arr
            except Exception:
                log.exception("camera capture failed")
            await asyncio.sleep(period)
