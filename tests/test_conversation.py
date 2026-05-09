import json
from pathlib import Path

import pytest

from conversation import ConversationLog


@pytest.fixture
def log(tmp_path: Path) -> ConversationLog:
    return ConversationLog(path=tmp_path / "conversation.json", max_turns=20)


def test_empty_on_first_load(log: ConversationLog):
    assert log.turns() == []


def test_append_returns_full_record(log: ConversationLog):
    log.append("hello", "hi")
    t = log.turns()[-1]
    assert t["user"] == "hello"
    assert t["assistant"] == "hi"
    assert isinstance(t["ts"], float)


def test_append_persists_to_disk(tmp_path: Path):
    a = ConversationLog(path=tmp_path / "c.json", max_turns=20)
    a.append("hello", "hi")
    b = ConversationLog(path=tmp_path / "c.json", max_turns=20)
    assert b.turns()[-1]["user"] == "hello"


def test_trim_to_max(tmp_path: Path):
    log = ConversationLog(path=tmp_path / "c.json", max_turns=3)
    for i in range(5):
        log.append(f"u{i}", f"a{i}")
    users = [t["user"] for t in log.turns()]
    assert users == ["u2", "u3", "u4"]


def test_recent_returns_last_n(log: ConversationLog):
    for i in range(5):
        log.append(f"u{i}", f"a{i}")
    recent = log.recent(2)
    assert [t["user"] for t in recent] == ["u3", "u4"]
