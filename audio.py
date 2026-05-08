"""Audio I/O for Gemini Live.

MicStream: yields 16 kHz mono int16 PCM bytes from the Snowball.
SpeakerStream: writes 24 kHz mono int16 PCM bytes to the HyperX dongle.

USB enumeration order on Pi 3B is unstable, so audio devices are resolved
by name substring + direction (input/output).
"""
from __future__ import annotations

import asyncio
import logging
from typing import AsyncIterator

import numpy as np
import sounddevice as sd

log = logging.getLogger(__name__)

MIC_RATE = 16000        # what Gemini Live wants in
GEMINI_OUT_RATE = 24000  # what Gemini Live emits
SPK_RATE = 48000        # what HyperX hw:2,0 accepts (we upsample 2x)
CHUNK_MS = 40           # 40 ms chunks → snappy turn-taking


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


class MicStream:
    """Async iterator yielding raw int16 PCM bytes at 16 kHz mono."""

    def __init__(self, device_substring: str) -> None:
        self._device_idx = find_device(device_substring, "input")
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=200)
        self._loop = asyncio.get_event_loop()
        self._stream: sd.RawInputStream | None = None

    def _callback(self, indata, frames, time_info, status) -> None:
        # Called from PortAudio thread. Push bytes onto async queue.
        if status:
            log.warning("mic status: %s", status)
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, bytes(indata))
        except asyncio.QueueFull:
            pass

    async def start(self) -> None:
        block = int(MIC_RATE * CHUNK_MS / 1000)
        self._stream = sd.RawInputStream(
            samplerate=MIC_RATE, blocksize=block, device=self._device_idx,
            channels=1, dtype="int16", callback=self._callback,
        )
        self._stream.start()

    async def stop(self) -> None:
        if self._stream:
            self._stream.stop()
            self._stream.close()

    async def chunks(self) -> AsyncIterator[bytes]:
        while True:
            yield await self._queue.get()


class SpeakerStream:
    """Plays raw int16 PCM bytes coming from Gemini Live (24 kHz mono).

    Internally upsamples to 48 kHz because the HyperX hw:2,0 device only
    accepts 48 kHz. 48/24 is an integer ratio, so np.repeat is fine.
    """

    def __init__(self, device_substring: str) -> None:
        self._device_idx = find_device(device_substring, "output")
        self._queue: asyncio.Queue[bytes] = asyncio.Queue(maxsize=400)
        self._stream: sd.RawOutputStream | None = None
        self._task: asyncio.Task | None = None
        self.is_speaking = False

    async def start(self) -> None:
        self._stream = sd.RawOutputStream(
            samplerate=SPK_RATE, device=self._device_idx, channels=1, dtype="int16",
        )
        self._stream.start()
        self._task = asyncio.create_task(self._writer())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
        if self._stream:
            self._stream.stop()
            self._stream.close()

    async def write(self, pcm_24k: bytes) -> None:
        # Upsample 24 kHz mono → 48 kHz mono by repeating each sample twice.
        arr = np.frombuffer(pcm_24k, dtype=np.int16)
        upsampled = np.repeat(arr, 2)
        await self._queue.put(upsampled.tobytes())

    async def flush(self) -> None:
        # Drain anything buffered. Useful when the model interrupts itself.
        while not self._queue.empty():
            try:
                self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break

    async def _writer(self) -> None:
        while True:
            pcm = await self._queue.get()
            self.is_speaking = True
            # sd.RawOutputStream.write is blocking; run in a thread.
            await asyncio.to_thread(self._stream.write, pcm)
            if self._queue.empty():
                self.is_speaking = False
