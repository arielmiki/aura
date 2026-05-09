"""Short-term conversation log: rolling window of the last N turns.

Persisted to JSON. Used as recent context in every Gemini prompt.
"""
from __future__ import annotations

import json
import time
from pathlib import Path


class ConversationLog:
    def __init__(self, path: Path, max_turns: int = 20) -> None:
        self.path = Path(path)
        self.max_turns = max_turns
        if self.path.exists():
            self._turns = json.loads(self.path.read_text())
        else:
            self._turns = []

    def turns(self) -> list[dict]:
        return list(self._turns)

    def append(self, user: str, assistant: str) -> None:
        self._turns.append({
            "user": user,
            "assistant": assistant,
            "ts": time.time(),
        })
        if len(self._turns) > self.max_turns:
            self._turns = self._turns[-self.max_turns:]
        self.path.write_text(json.dumps(self._turns, indent=2))

    def recent(self, n: int) -> list[dict]:
        return list(self._turns[-n:])
