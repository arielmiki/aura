import json
from pathlib import Path

import pytest

from vocab import VocabStore


@pytest.fixture
def store(tmp_path: Path) -> VocabStore:
    return VocabStore(path=tmp_path / "vocab.json", seed=["YES", "NO"])


def test_seed_words_loaded(store: VocabStore):
    assert store.words() == ["YES", "NO"]


def test_learn_word_appends(store: VocabStore):
    store.learn_word("PEN", "long thin object")
    assert "PEN" in store.words()


def test_learn_word_normalizes_to_uppercase(store: VocabStore):
    store.learn_word("pen", "writing tool")
    assert "PEN" in store.words()


def test_learn_word_duplicate_is_idempotent(store: VocabStore):
    store.learn_word("PEN", "first description")
    store.learn_word("PEN", "second description")
    assert store.words().count("PEN") == 1


def test_learn_word_persists_to_disk(tmp_path):
    s1 = VocabStore(path=tmp_path / "vocab.json", seed=["YES"])
    s1.learn_word("MUG", "ceramic cup")
    s2 = VocabStore(path=tmp_path / "vocab.json", seed=["YES"])
    assert "MUG" in s2.words()


def test_existing_file_takes_priority_over_seed(tmp_path):
    p = tmp_path / "vocab.json"
    p.write_text(json.dumps([{"word": "PEN", "description": "x", "learned_at": 1.0}]))
    s = VocabStore(path=p, seed=["YES", "NO"])
    assert s.words() == ["PEN"]


def test_entries_returns_full_records(store: VocabStore):
    store.learn_word("PEN", "long thin object")
    entries = store.entries()
    assert entries[-1]["word"] == "PEN"
    assert entries[-1]["description"] == "long thin object"
    assert isinstance(entries[-1]["learned_at"], float)


@pytest.mark.asyncio
async def test_subscribe_receives_new_word(store: VocabStore):
    queue = await store.subscribe()
    store.learn_word("PEN", "long thin object")
    event = await queue.get()
    assert event["type"] == "word_learned"
    assert event["word"] == "PEN"
