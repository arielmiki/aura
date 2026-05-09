"""Vocab store: persistent JSON-backed list of words Rocky knows.

Subscribers (the web UI, possibly idle logic) get asyncio.Queue events
when new words are learned.
"""
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path
from typing import Iterable


class VocabStore:
    def __init__(self, path: Path, seed: Iterable[str] = ()) -> None:
        self.path = Path(path)
        self._entries: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []

        if self.path.exists():
            self._entries = json.loads(self.path.read_text())
        else:
            now = time.time()
            self._entries = [
                {"word": w.upper(), "description": "seed", "learned_at": now}
                for w in seed
            ]
            self._save()

    def words(self) -> list[str]:
        return [e["word"] for e in self._entries]

    def entries(self) -> list[dict]:
        return list(self._entries)

    def learn_word(self, word: str, description: str) -> bool:
        word = word.strip().upper()
        if not word:
            return False
        if word in self.words():
            return False
        entry = {"word": word, "description": description, "learned_at": time.time()}
        self._entries.append(entry)
        self._save()
        self._broadcast({"type": "word_learned", "word": word, "entry": entry})
        return True

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def set_status(self, status: str) -> None:
        self._broadcast({"type": "status", "status": status})

    def _broadcast(self, event: dict) -> None:
        for q in self._subscribers:
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass

    def _save(self) -> None:
        self.path.write_text(json.dumps(self._entries, indent=2))
