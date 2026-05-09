from pathlib import Path

import pytest

from patterns import PatternStore


@pytest.fixture
def store(tmp_path: Path) -> PatternStore:
    return PatternStore(path=tmp_path / "patterns.json")


def test_default_state_has_max_words(store: PatternStore):
    assert store.state()["max_reply_words"] == 8


def test_shorter_signal_decreases_max_words(store: PatternStore):
    before = store.state()["max_reply_words"]
    store.apply_user_signal("rocky shorter please")
    assert store.state()["max_reply_words"] == before - 3
    assert store.state()["shorter_count"] == 1


def test_repeated_shorter_signals_clamp_at_floor(store: PatternStore):
    for _ in range(10):
        store.apply_user_signal("be more brief")
    assert store.state()["max_reply_words"] == 3


def test_longer_signal_increases_max_words(store: PatternStore):
    before = store.state()["max_reply_words"]
    store.apply_user_signal("can you elaborate more")
    assert store.state()["max_reply_words"] == before + 5
    assert store.state()["longer_count"] == 1


def test_topic_tracking_counts_content_words(store: PatternStore):
    store.apply_user_signal("my dog Lily is a corgi")
    store.apply_user_signal("Lily likes treats")
    counts = store.state()["topic_counts"]
    assert counts.get("lily", 0) >= 2


def test_topic_tracking_skips_stopwords(store: PatternStore):
    store.apply_user_signal("Rocky please tell me about the thing")
    counts = store.state()["topic_counts"]
    # All of these are stopwords
    assert "rocky" not in counts
    assert "please" not in counts
    assert "tell" not in counts
    assert "about" not in counts


def test_state_persists_across_instances(tmp_path: Path):
    a = PatternStore(path=tmp_path / "p.json")
    a.apply_user_signal("shorter please")
    b = PatternStore(path=tmp_path / "p.json")
    assert b.state()["shorter_count"] == 1


def test_render_includes_word_limit(store: PatternStore):
    rendered = store.render_for_prompt()
    assert "Preferred reply length" in rendered
    assert "8" in rendered


def test_render_mentions_shorter_count_after_signal(store: PatternStore):
    store.apply_user_signal("shorter please")
    rendered = store.render_for_prompt()
    assert "shorter" in rendered.lower()


def test_render_includes_top_topics(store: PatternStore):
    for _ in range(3):
        store.apply_user_signal("Lily corgi dog")
    rendered = store.render_for_prompt()
    assert "lily" in rendered.lower() or "corgi" in rendered.lower()


def test_no_signal_returns_false(store: PatternStore):
    assert store.apply_user_signal("") is False


@pytest.mark.asyncio
async def test_subscribe_receives_pattern_update(store: PatternStore):
    q = await store.subscribe()
    store.apply_user_signal("shorter please")
    event = await q.get()
    assert event["type"] == "pattern_updated"
    assert "state" in event
