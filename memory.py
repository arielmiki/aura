"""Memory store: persistent JSON-backed list of facts the assistant remembers
about the user. Each memory may also have a snapshot of what the camera saw
at the moment the fact was learned.

Subscribers (the web UI) get asyncio.Queue events when new memories are saved.
"""
from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path
from typing import Iterable, Optional


class MemoryStore:
    def __init__(self, path: Path, image_dir: Optional[Path] = None) -> None:
        self.path = Path(path)
        # Default: alongside memories.json, in `<path stem>_images/`
        self.image_dir = Path(image_dir) if image_dir else self.path.with_name(
            self.path.stem + "_images"
        )
        self.image_dir.mkdir(parents=True, exist_ok=True)
        self._entries: list[dict] = []
        self._subscribers: list[asyncio.Queue] = []

        if self.path.exists():
            self._entries = json.loads(self.path.read_text())

    def facts(self) -> list[str]:
        return [e["fact"] for e in self._entries]

    def entries(self) -> list[dict]:
        return list(self._entries)

    def remember(self, fact: str, image_jpeg: Optional[bytes] = None) -> Optional[str]:
        """Add a fact, optionally with a JPEG snapshot.

        Returns the new entry id, or None if the fact is empty or duplicates
        an existing fact (case-insensitive exact match).
        """
        fact = fact.strip()
        if not fact:
            return None
        existing = {f.lower() for f in self.facts()}
        if fact.lower() in existing:
            return None
        entry_id = uuid.uuid4().hex[:8]
        entry: dict = {
            "id": entry_id,
            "fact": fact,
            "saved_at": time.time(),
        }
        # Persist the snapshot if provided.
        if image_jpeg:
            try:
                (self.image_dir / f"{entry_id}.jpg").write_bytes(image_jpeg)
                entry["has_image"] = True
            except OSError:
                # Don't fail the memory if the image write fails.
                pass
        self._entries.append(entry)
        self._save()
        self._broadcast({"type": "memory_added", "entry": entry})
        return entry_id

    def image_path(self, entry_id: str) -> Optional[Path]:
        """Return the path to the snapshot for `entry_id`, or None if missing."""
        p = self.image_dir / f"{entry_id}.jpg"
        return p if p.exists() else None

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
