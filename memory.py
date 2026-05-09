"""Memory store: persistent JSON-backed list of facts the assistant remembers
about the user.

Subscribers (the web UI) get asyncio.Queue events when new memories are saved.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Iterable


class MemoryStore:
    def __init__(self, path: Path) -> None:
        self.path = Path(path)
        self._entries: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []

        if self.path.exists():
            self._entries = json.loads(self.path.read_text())

    def facts(self) -> list[str]:
        return [e["fact"] for e in self._entries]

    def entries(self) -> list[dict]:
        return list(self._entries)

    def remember(self, fact: str) -> str | None:
        """Add a fact. Returns the new entry id, or None if the fact is empty
        or duplicates an existing fact (case-insensitive exact match)."""
        fact = fact.strip()
        if not fact:
            return None
        existing = {f.lower() for f in self.facts()}
        if fact.lower() in existing:
            return None
        entry = {
            "id": uuid.uuid4().hex[:8],
            "fact": fact,
            "saved_at": time.time(),
        }
        self._entries.append(entry)
        self._save()
        self._broadcast({"type": "memory_added", "entry": entry})
        return entry["id"]

    def replace_all(self, new_entries: Iterable[dict]) -> None:
        """Used by background compaction to swap out the entire memory list."""
        self._entries = list(new_entries)
        self._save()
        self._broadcast({"type": "memory_compacted", "count": len(self._entries)})

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
        self.path.write_text(json.dumps(self._entries, indent=2))
