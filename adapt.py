"""Adaption Labs integration with categorized, hash-gated uploads.

Adaption's API only supports creating new datasets — there is no append
or delete. To keep the dashboard tidy and avoid uploading identical data
repeatedly, we:

1. Split the corpus into three named categories:
     - rocky-chat       — {prompt, completion} from the conversation log
     - rocky-memories   — {fact, saved_at} from MemoryStore
     - rocky-patterns   — {key, value} from PatternStore

2. Hash each category's CSV content. If the hash matches the last
   successful upload for that category, skip it. Only changed
   categories upload, so a quiet conversation produces zero new
   datasets.

3. When at least one category does upload, the adapted result of the
   chat category is downloaded and fed back into Rocky's prompt as
   [ADAPTED KNOWLEDGE], same as before.
"""
from __future__ import annotations

import asyncio
import csv
import hashlib
import io
import json
import logging
import os
import time
from pathlib import Path
from typing import Optional


log = logging.getLogger(__name__)

CATEGORIES = ("chat", "memories", "patterns")


class AdaptError(RuntimeError):
    pass


class Adapter:
    """Lazy SDK client + categorized state tracking."""

    def __init__(self,
                 blueprint_path: Path,
                 csv_dir: Path = Path("/tmp/rocky_corpus"),
                 cache_path: Path = Path("adapted_knowledge.json"),
                 api_key: Optional[str] = None) -> None:
        self.blueprint_path = Path(blueprint_path)
        self.csv_dir = Path(csv_dir)
        self.csv_dir.mkdir(parents=True, exist_ok=True)
        self.cache_path = Path(cache_path)
        self._api_key = api_key or os.environ.get("ADAPTION_API_KEY")
        self._client = None
        self._subscribers: list[asyncio.Queue] = []
        self._adapted_rows: list[dict] = []
        if self.cache_path.exists():
            try:
                self._adapted_rows = json.loads(self.cache_path.read_text())
            except (json.JSONDecodeError, OSError):
                pass
        self._categories: dict[str, dict] = {
            cat: {
                "status": "idle",
                "row_count": 0,
                "dataset_id": None,
                "last_hash": None,
                "updated_at": None,
            }
            for cat in CATEGORIES
        }

    @property
    def configured(self) -> bool:
        return bool(self._api_key)

    def state(self) -> dict:
        # Aggregate "in-flight" status from any category currently working.
        statuses = [c["status"] for c in self._categories.values()]
        if any(s == "uploading" for s in statuses):
            agg = "uploading"
        elif any(s == "running" for s in statuses):
            agg = "running"
        elif any(s == "failed" for s in statuses):
            agg = "failed"
        elif any(s == "completed" for s in statuses):
            agg = "completed"
        else:
            agg = "idle"
        return {
            "status": agg,
            "categories": {k: dict(v) for k, v in self._categories.items()},
            "adapted_count": len(self._adapted_rows),
            "configured": self.configured,
        }

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

    # ---- CSV builders for each category --------------------------------

    @staticmethod
    def _build_chat_csv(turns: list[dict]) -> tuple[str, int]:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["prompt", "completion"])
        rows = 0
        for t in turns:
            user = (t.get("user") or "").strip()
            asst = (t.get("assistant") or "").strip()
            if user and asst:
                w.writerow([user, asst])
                rows += 1
        return buf.getvalue(), rows

    @staticmethod
    def _build_memories_csv(entries: list[dict]) -> tuple[str, int]:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["prompt", "completion"])
        rows = 0
        for e in entries:
            fact = (e.get("fact") or "").strip()
            if fact:
                w.writerow(["What do you remember about the user?", fact])
                rows += 1
        return buf.getvalue(), rows

    @staticmethod
    def _build_patterns_csv(state: dict) -> tuple[str, int]:
        buf = io.StringIO()
        w = csv.writer(buf)
        w.writerow(["prompt", "completion"])
        rows = 0
        if (mw := state.get("max_reply_words")):
            w.writerow(["What is the user's preferred reply length?",
                        f"At most {mw} words per reply."])
            rows += 1
        if (sc := state.get("shorter_count", 0)) > 0:
            w.writerow(["Has the user asked for shorter replies?",
                        f"Yes — {sc} time(s)."])
            rows += 1
        if (lc := state.get("longer_count", 0)) > 0:
            w.writerow(["Has the user asked for more detail?",
                        f"Yes — {lc} time(s)."])
            rows += 1
        topics = sorted(
            ((k, v) for k, v in (state.get("topic_counts") or {}).items() if v >= 2),
            key=lambda kv: kv[1], reverse=True,
        )[:5]
        for topic, count in topics:
            w.writerow([f"Does the user mention '{topic}' often?",
                        f"Yes — {count} times."])
            rows += 1
        return buf.getvalue(), rows

    # ---- Main adapt entry point ----------------------------------------

    async def adapt(self,
                    turns: list[dict],
                    memory_entries: list[dict],
                    patterns_state: dict) -> dict:
        """Run an adapt cycle across all three categories, skipping any
        whose content hash hasn't changed since the last successful run."""
        client = self._client_or_raise()
        sources = {
            "chat":     self._build_chat_csv(turns),
            "memories": self._build_memories_csv(memory_entries),
            "patterns": self._build_patterns_csv(patterns_state),
        }
        results = {}
        for cat, (csv_text, rows) in sources.items():
            cat_state = self._categories[cat]
            if rows == 0:
                results[cat] = "empty"
                continue
            h = hashlib.sha256(csv_text.encode("utf-8")).hexdigest()
            if h == cat_state["last_hash"]:
                results[cat] = "skipped (unchanged)"
                log.info("adapt[%s]: hash unchanged, skipping", cat)
                continue
            results[cat] = await self._adapt_one(client, cat, csv_text, rows, h)
        log.info("adapt cycle: %s", results)
        self._broadcast()
        return self.state()

    async def _adapt_one(self, client, cat: str, csv_text: str,
                         rows: int, content_hash: str) -> str:
        """Upload + run a single category. Returns a short status string."""
        path = self.csv_dir / f"rocky-{cat}.csv"
        await asyncio.to_thread(path.write_text, csv_text)
        cat_state = self._categories[cat]
        cat_state.update({"status": "uploading", "updated_at": time.time(),
                          "row_count": rows})
        self._broadcast()
        try:
            upload = await asyncio.to_thread(
                client.datasets.upload_file,
                str(path),
                name=f"rocky-{cat}-{int(time.time())}",
            )
            dataset_id = (
                getattr(upload, "dataset_id", None)
                or getattr(upload, "id", None)
            )
            if not dataset_id:
                raise AdaptError(f"upload returned no dataset id: {upload!r}")

            blueprint = ""
            if self.blueprint_path.exists():
                blueprint = self.blueprint_path.read_text()[:4000]

            await asyncio.to_thread(
                client.datasets.run,
                dataset_id,
                column_mapping={"prompt": "prompt", "completion": "completion"},
                brand_controls={"blueprint": blueprint},
            )
            cat_state.update({
                "status": "running",
                "dataset_id": dataset_id,
                "last_hash": content_hash,
                "updated_at": time.time(),
            })
            log.info("adapt[%s]: dataset=%s, %d rows", cat, dataset_id, rows)
            self._broadcast()
            return f"uploaded ({rows} rows)"
        except Exception as e:
            log.exception("adapt[%s] failed", cat)
            cat_state.update({"status": "failed", "updated_at": time.time()})
            self._broadcast()
            return f"failed: {type(e).__name__}"

    async def refresh(self) -> dict:
        """Poll status for any in-flight category dataset. When the chat
        category completes, download its adapted rows and cache them as
        the [ADAPTED KNOWLEDGE] block."""
        if not self.configured:
            return self.state()
        client = self._client_or_raise()
        for cat, cat_state in self._categories.items():
            ds_id = cat_state.get("dataset_id")
            if not ds_id or cat_state.get("status") in ("idle", "completed", "failed"):
                continue
            try:
                resp = await asyncio.to_thread(client.datasets.get_status, ds_id)
                raw = (getattr(resp, "status", None)
                       or getattr(resp, "state", None) or str(resp))
                status = str(raw).lower()
                if any(k in status for k in ("done", "complete", "success")):
                    normalized = "completed"
                elif any(k in status for k in ("fail", "error")):
                    normalized = "failed"
                elif any(k in status for k in ("run", "process", "pending", "queue")):
                    normalized = "running"
                else:
                    normalized = "running"
                old = cat_state["status"]
                cat_state.update({"status": normalized, "updated_at": time.time()})
                if normalized != old:
                    self._broadcast()
                if cat == "chat" and normalized == "completed" and old != "completed":
                    asyncio.create_task(self._download_and_cache(ds_id))
            except Exception:
                log.exception("adapt[%s] status poll failed", cat)
        return self.state()

    async def _download_and_cache(self, dataset_id: str) -> None:
        try:
            client = self._client_or_raise()
            url = await asyncio.to_thread(
                client.datasets.download, dataset_id, file_format="csv",
            )
            import urllib.request
            with urllib.request.urlopen(url, timeout=30) as r:
                body = r.read().decode("utf-8", errors="replace")
            reader = csv.DictReader(io.StringIO(body))
            rows = []
            for r in reader:
                p = (r.get("prompt") or "").strip()
                c = (r.get("completion") or "").strip()
                if p and c:
                    rows.append({"prompt": p, "completion": c})
            self._adapted_rows = rows
            self.cache_path.write_text(json.dumps(rows, indent=2))
            log.info("adapt: cached %d adapted rows", len(rows))
            self._broadcast()
        except Exception:
            log.exception("adapt: download/cache failed")

    def adapted_knowledge(self, max_rows: int = 8) -> list[dict]:
        return list(self._adapted_rows[-max_rows:])

    def render_for_prompt(self, max_rows: int = 8) -> str:
        rows = self.adapted_knowledge(max_rows)
        if not rows:
            return "(no adapted history yet)"
        return "\n".join(
            f"- Past: \"{r['prompt']}\" -> \"{r['completion']}\""
            for r in rows
        )
