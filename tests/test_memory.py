import json
from pathlib import Path

import pytest

from memory import MemoryStore


@pytest.fixture
def store(tmp_path: Path) -> MemoryStore:
    return MemoryStore(path=tmp_path / "memories.json")


def test_empty_on_first_load(store: MemoryStore):
    assert store.facts() == []


def test_remember_appends(store: MemoryStore):
    mid = store.remember("User has a corgi named Lily")
    assert mid is not None
    assert "User has a corgi named Lily" in store.facts()


def test_remember_strips_whitespace(store: MemoryStore):
    store.remember("  User likes coffee   ")
    assert store.facts() == ["User likes coffee"]


def test_remember_empty_returns_none(store: MemoryStore):
    assert store.remember("") is None
    assert store.remember("   ") is None
    assert store.facts() == []


def test_remember_duplicate_returns_none(store: MemoryStore):
    store.remember("User likes coffee")
    second = store.remember("user LIKES coffee")  # case-insensitive duplicate
    assert second is None
    assert store.facts().count("User likes coffee") == 1


def test_remember_persists_to_disk(tmp_path: Path):
    s1 = MemoryStore(path=tmp_path / "memories.json")
    s1.remember("User has a corgi named Lily")
    s2 = MemoryStore(path=tmp_path / "memories.json")
    assert "User has a corgi named Lily" in s2.facts()


def test_existing_file_is_loaded(tmp_path: Path):
    p = tmp_path / "memories.json"
    p.write_text(json.dumps([{"id": "abc", "fact": "x", "saved_at": 1.0}]))
    s = MemoryStore(path=p)
    assert s.facts() == ["x"]


def test_entries_returns_full_records(store: MemoryStore):
    store.remember("User has a corgi named Lily")
    e = store.entries()[-1]
    assert e["fact"] == "User has a corgi named Lily"
    assert isinstance(e["saved_at"], float)
    assert isinstance(e["id"], str) and len(e["id"]) > 0


def test_replace_all_swaps_contents(store: MemoryStore):
    store.remember("a")
    store.remember("b")
    store.replace_all([{"id": "z", "fact": "c", "saved_at": 1.0}])
    assert store.facts() == ["c"]


@pytest.mark.asyncio
async def test_subscribe_receives_memory_added(store: MemoryStore):
    queue = await store.subscribe()
    store.remember("User has a corgi named Lily")
    event = await queue.get()
    assert event["type"] == "memory_added"
    assert event["entry"]["fact"] == "User has a corgi named Lily"


@pytest.mark.asyncio
async def test_subscribe_receives_compaction(store: MemoryStore):
    queue = await store.subscribe()
    store.replace_all([{"id": "z", "fact": "c", "saved_at": 1.0}])
    event = await queue.get()
    assert event["type"] == "memory_compacted"
    assert event["count"] == 1
