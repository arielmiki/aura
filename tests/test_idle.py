import time
import pytest

from idle import IdleGate


def test_does_not_fire_when_recently_active():
    gate = IdleGate(silence_secs=30, motion_threshold=5.0, cooldown_secs=60)
    gate.note_user_audio(time.time())
    assert not gate.should_fire(now=time.time(), motion=10.0)


def test_does_not_fire_with_no_motion():
    gate = IdleGate(silence_secs=30, motion_threshold=5.0, cooldown_secs=60)
    t0 = time.time() - 100
    gate.note_user_audio(t0)
    assert not gate.should_fire(now=time.time(), motion=1.0)


def test_fires_when_silent_and_motion():
    gate = IdleGate(silence_secs=30, motion_threshold=5.0, cooldown_secs=60)
    t0 = time.time() - 100
    gate.note_user_audio(t0)
    assert gate.should_fire(now=time.time(), motion=10.0)


def test_cooldown_blocks_repeat_fires():
    gate = IdleGate(silence_secs=30, motion_threshold=5.0, cooldown_secs=60)
    t0 = time.time() - 100
    gate.note_user_audio(t0)
    now = time.time()
    assert gate.should_fire(now=now, motion=10.0)
    gate.note_fired(now=now)
    assert not gate.should_fire(now=now + 30, motion=10.0)
    assert gate.should_fire(now=now + 70, motion=10.0)
