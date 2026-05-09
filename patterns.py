"""User patterns store: derived adaptive signals fed back into Rocky's prompt.

Signals are detected heuristically from each user transcript:
- "shorter please" / "too long" / "be brief"  -> tighten max_reply_words
- "longer" / "more detail" / "elaborate"      -> loosen max_reply_words
- "stop using X word"                         -> add to forbidden_words
- frequent topic mentions                     -> tracked counts

The state is persisted to JSON and rendered into a `[USER PATTERNS]` block
in the system prompt before every Gemini call.
"""
from __future__ import annotations

import asyncio
import json
import re
import time
from pathlib import Path
from typing import Optional


SHORTER_HINTS = [
    "shorter", "too long", "brief", "less word", "fewer word",
    "concise", "shut up", "simpler", "tldr",
]
LONGER_HINTS = [
    "longer", "more detail", "elaborate", "expand", "more thorough",
]

DEFAULT_STATE = {
    "max_reply_words": 8,
    "shorter_count": 0,
    "longer_count": 0,
    "topic_counts": {},     # word -> count for noun-ish tokens
    "last_updated": 0.0,
}


class PatternStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._state: dict = dict(DEFAULT_STATE)
        self._subscribers: list[asyncio.Queue] = []
        if self.path.exists():
            try:
                loaded = json.loads(self.path.read_text())
                self._state.update(loaded)
            except json.JSONDecodeError:
                pass

    def state(self) -> dict:
        return dict(self._state)

    def apply_user_signal(self, transcript: str) -> bool:
        """Detect adaptive signals in `transcript` and update state.
        Returns True if anything changed."""
        if not transcript:
            return False
        text = transcript.lower()
        changed = False

        if any(h in text for h in SHORTER_HINTS):
            new_max = max(3, self._state["max_reply_words"] - 3)
            if new_max != self._state["max_reply_words"]:
                self._state["max_reply_words"] = new_max
                changed = True
            self._state["shorter_count"] = self._state.get("shorter_count", 0) + 1
            changed = True

        if any(h in text for h in LONGER_HINTS):
            new_max = min(30, self._state["max_reply_words"] + 5)
            if new_max != self._state["max_reply_words"]:
                self._state["max_reply_words"] = new_max
                changed = True
            self._state["longer_count"] = self._state.get("longer_count", 0) + 1
            changed = True

        # Cheap topic-frequency tracking — words >=4 chars, not common stop
        # words, used as proxies for what the user keeps bringing up.
        topics = self._state.setdefault("topic_counts", {})
        for tok in _content_tokens(text):
            topics[tok] = topics.get(tok, 0) + 1
            changed = True

        if changed:
            self._state["last_updated"] = time.time()
            self._save()
            self._broadcast({"type": "pattern_updated", "state": self.state()})
        return changed

    def render_for_prompt(self) -> str:
        """Format patterns as a short bulleted block for the system prompt."""
        lines: list[str] = []
        lines.append(f"- Preferred reply length: at most {self._state['max_reply_words']} words")
        sc = self._state.get("shorter_count", 0)
        lc = self._state.get("longer_count", 0)
        if sc:
            lines.append(f"- The user has asked for shorter replies {sc} time(s). Honor that.")
        if lc:
            lines.append(f"- The user has asked for more detail {lc} time(s).")
        top = sorted(
            self._state.get("topic_counts", {}).items(),
            key=lambda kv: kv[1], reverse=True,
        )[:3]
        if any(c >= 2 for _, c in top):
            top_str = ", ".join(f"{w} (×{c})" for w, c in top if c >= 2)
            lines.append(f"- Frequent topics: {top_str}.")
        return "\n".join(lines)

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _broadcast(self, event: dict) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._state, indent=2))


_STOP = {
    "the", "and", "you", "are", "for", "this", "that", "with", "have", "from",
    "your", "what", "when", "rocky", "tell", "about", "just", "really", "just",
    "yeah", "okay", "please", "thank", "thanks", "they", "them", "their",
    "there", "where", "which", "would", "could", "should", "much", "many",
    "some", "very", "more", "most", "than", "then", "now", "but", "also",
}

_TOKEN = re.compile(r"\b[a-zA-Z]{4,}\b")


def _content_tokens(text: str) -> list[str]:
    """Words >= 4 chars not in stop set, lowercased."""
    return [t.lower() for t in _TOKEN.findall(text) if t.lower() not in _STOP]
