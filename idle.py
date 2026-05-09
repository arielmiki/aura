"""Idle gate: decides whether Rocky should make a spontaneous comment."""
from __future__ import annotations

import asyncio
import logging
import time

log = logging.getLogger(__name__)


class IdleGate:
    def __init__(self, silence_secs: float = 30.0,
                 motion_threshold: float = 5.0,
                 cooldown_secs: float = 60.0) -> None:
        self.silence_secs = silence_secs
        self.motion_threshold = motion_threshold
        self.cooldown_secs = cooldown_secs
        self._last_user_audio = time.time()
        self._last_fired = 0.0

    def note_user_audio(self, now: float | None = None) -> None:
        self._last_user_audio = now if now is not None else time.time()

    def note_fired(self, now: float | None = None) -> None:
        self._last_fired = now if now is not None else time.time()

    def should_fire(self, now: float, motion: float) -> bool:
        if (now - self._last_user_audio) < self.silence_secs:
            return False
        if motion < self.motion_threshold:
            return False
        if (now - self._last_fired) < self.cooldown_secs:
            return False
        return True


async def run_idle_loop(gate: IdleGate, camera, brain, get_silent_for) -> None:
    """Background task. `get_silent_for()` returns elapsed silence seconds."""
    while True:
        await asyncio.sleep(2.0)
        now = time.time()
        motion = camera.motion_score()
        if gate.should_fire(now=now, motion=motion):
            log.info("idle gate firing (motion=%.1f)", motion)
            await brain.send_text(
                "(The human is quiet but you can see them. "
                "Make a brief 1 to 3 word observation using only your vocabulary.)"
            )
            gate.note_fired(now=now)
