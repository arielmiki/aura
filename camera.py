"""Camera service: keeps the latest JPEG frame in memory and computes a
crude motion score by comparing successive frames.

Runs as an asyncio task. Captures at ~1 fps which is plenty for our use.
"""
from __future__ import annotations

import asyncio
import io
import logging
from typing import Optional

import numpy as np
from PIL import Image
from picamera2 import Picamera2
from libcamera import Transform

log = logging.getLogger(__name__)


class CameraService:
    def __init__(self, size: tuple[int, int] = (640, 480),
                 hflip: bool = False, vflip: bool = False,
                 fps: float = 1.0) -> None:
        self._size = size
        self._fps = fps
        self._cam = Picamera2()
        cfg = self._cam.create_still_configuration(
            main={"size": size, "format": "RGB888"},
            transform=Transform(hflip=hflip, vflip=vflip),
        )
        self._cam.configure(cfg)
        self._latest_jpeg: Optional[bytes] = None
        self._latest_array: Optional[np.ndarray] = None
        self._prev_array: Optional[np.ndarray] = None
        self._task: Optional[asyncio.Task] = None
        self._motion_score = 0.0

    async def start(self) -> None:
        self._cam.start()
        await asyncio.sleep(1.0)  # auto exposure settle
        self._task = asyncio.create_task(self._loop())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        self._cam.stop()

    def latest_jpeg(self) -> Optional[bytes]:
        return self._latest_jpeg

    def motion_score(self) -> float:
        return self._motion_score

    async def _loop(self) -> None:
        period = 1.0 / self._fps
        while True:
            try:
                arr = self._cam.capture_array()  # RGB888 numpy array
                buf = io.BytesIO()
                Image.fromarray(arr).save(buf, format="JPEG", quality=80)
                self._latest_jpeg = buf.getvalue()
                if self._prev_array is not None and self._prev_array.shape == arr.shape:
                    diff = np.abs(arr.astype(np.int16) - self._prev_array.astype(np.int16))
                    self._motion_score = float(diff.mean())
                self._prev_array = self._latest_array
                self._latest_array = arr
            except Exception:
                log.exception("camera capture failed")
            await asyncio.sleep(period)
