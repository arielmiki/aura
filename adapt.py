"""Adaption Labs integration.

Treats Rocky's conversation log as a growing personal training corpus. We
periodically (or on demand) export it as a CSV of {prompt, completion}
rows, upload to Adaption, and start an adaptation run with the Rocky
persona prompt as the `blueprint`. Adaption returns a quality-checked,
fine-tuning-ready dataset that future Rocky models can train on.

The integration is best-effort: failures don't break the chat path. The
`Adapter` reports a small state dict the UI subscribes to.
"""
from __future__ import annotations

import asyncio
import csv
import io
import logging
import os
import time
from pathlib import Path
from typing import Optional


log = logging.getLogger(__name__)


class AdaptError(RuntimeError):
    pass


class Adapter:
    """Lazy SDK client + state for the most recent adaptation run."""

    def __init__(self,
                 blueprint_path: Path,
                 csv_path: Path = Path("/tmp/rocky_corpus.csv"),
                 api_key: Optional[str] = None) -> None:
        self.blueprint_path = Path(blueprint_path)
        self.csv_path = Path(csv_path)
        self._api_key = api_key or os.environ.get("ADAPTION_API_KEY")
        self._client = None  # built lazily so the app boots without the key
        self._subscribers: list[asyncio.Queue] = []
        self._state: dict = {
            "status": "idle",   # idle | uploading | running | completed | failed
            "row_count": 0,
            "dataset_id": None,
            "started_at": None,
            "updated_at": None,
            "error": None,
            "configured": bool(self._api_key),
        }

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def state(self) -> dict:
        return dict(self._state)

    async def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        self._subscribers.append(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        if q in self._subscribers:
            self._subscribers.remove(q)

    def _broadcast(self) -> None:
        ev = {"type": "adapt_status", "state": self.state()}
        for q in self._subscribers:
            try:
                q.put_nowait(ev)
            except asyncio.QueueFull:
                pass

    def _client_or_raise(self):
        if not self.configured:
            raise AdaptError("ADAPTION_API_KEY not set")
        if self._client is None:
            from adaption import Adaption
            self._client = Adaption(api_key=self._api_key)
        return self._client

    def _build_csv(self, turns: list[dict]) -> int:
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(["prompt", "completion"])
        rows = 0
        for t in turns:
            user = (t.get("user") or "").strip()
            asst = (t.get("assistant") or "").strip()
            if user and asst:
                writer.writerow([user, asst])
                rows += 1
        self.csv_path.write_text(buf.getvalue())
        return rows

    async def adapt(self, turns: list[dict]) -> dict:
        """Export `turns` to CSV, upload, and start an adaptation run.
        Returns the new state. Does NOT block on completion."""
        client = self._client_or_raise()
        if not turns:
            raise AdaptError("no conversation turns yet")

        # Update status: uploading
        self._state.update({
            "status": "uploading",
            "started_at": time.time(),
            "updated_at": time.time(),
            "error": None,
        })
        self._broadcast()

        rows = await asyncio.to_thread(self._build_csv, turns)
        if rows == 0:
            self._state.update({"status": "failed",
                                "error": "no usable rows in conversation log",
                                "updated_at": time.time()})
            self._broadcast()
            raise AdaptError("no usable rows in conversation log")

        log.info("adapt: built CSV with %d rows at %s", rows, self.csv_path)

        # Upload
        upload = await asyncio.to_thread(
            client.datasets.upload_file,
            str(self.csv_path),
            name=f"rocky-corpus-{int(time.time())}",
        )
        dataset_id = (
            getattr(upload, "dataset_id", None)
            or getattr(upload, "id", None)
            or getattr(upload, "Id", None)
        )
        if not dataset_id:
            self._state.update({"status": "failed",
                                "error": "upload returned no dataset id",
                                "updated_at": time.time()})
            self._broadcast()
            raise AdaptError(f"upload returned: {upload!r}")

        log.info("adapt: uploaded, dataset_id=%s", dataset_id)

        # Read blueprint (cap length so the API doesn't reject huge prompts)
        blueprint = ""
        if self.blueprint_path.exists():
            blueprint = self.blueprint_path.read_text()[:4000]

        # Run
        await asyncio.to_thread(
            client.datasets.run,
            dataset_id,
            column_mapping={"prompt": "prompt", "completion": "completion"},
            brand_controls={"blueprint": blueprint},
        )

        log.info("adapt: run started for dataset_id=%s", dataset_id)
        self._state.update({
            "status": "running",
            "row_count": rows,
            "dataset_id": dataset_id,
            "updated_at": time.time(),
        })
        self._broadcast()
        return self.state()

    async def refresh(self) -> dict:
        """Re-poll Adaption for the current run's status. No-op if no run."""
        if not self.configured or not self._state.get("dataset_id"):
            return self.state()
        try:
            client = self._client_or_raise()
            resp = await asyncio.to_thread(
                client.datasets.get_status,
                self._state["dataset_id"],
            )
            # The response shape is opaque in 0.3.x; pull a status field if any.
            raw = (
                getattr(resp, "status", None)
                or getattr(resp, "state", None)
                or str(resp)
            )
            status = str(raw).lower()
            # Normalize a few common Adaption status strings into our buckets.
            if any(k in status for k in ("done", "complete", "success")):
                normalized = "completed"
            elif any(k in status for k in ("fail", "error")):
                normalized = "failed"
            elif any(k in status for k in ("run", "process", "pending", "queue")):
                normalized = "running"
            else:
                normalized = status or "running"
            old = self._state["status"]
            self._state.update({"status": normalized, "updated_at": time.time()})
            if normalized != old:
                self._broadcast()
        except Exception as e:
            log.exception("adapt: status fetch failed")
            self._state.update({"error": f"{type(e).__name__}: {e}",
                                "updated_at": time.time()})
            self._broadcast()
        return self.state()
