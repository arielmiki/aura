# Rocky Personal Assistant Implementation Plan (v2)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Browser-based personal assistant with a "Rocky" persona (ElevenLabs voice), STT→Gemini Flash→TTS pipeline, and a JSON-backed adaptive memory.

**Architecture:** All real-time audio/video stays in the browser (`getUserMedia` + Web Audio VAD + `MediaRecorder`). Backend is a stateless FastAPI service whose `POST /turn` accepts a WAV + JPEG and returns MP3. Memories persist in `memories.json`, fed into every system prompt; Gemini Flash decides when to save new memories via a `remember(fact)` tool call. Same backend deployable to Pi (`make sync`/`make setup`/`make run`) — Pi-specific scripts stay untouched.

**Tech Stack:** Python 3.13, FastAPI, `google-genai` (Gemini 2.5 Flash), `elevenlabs` (Scribe STT + Turbo TTS), vanilla JS in the browser (`MediaRecorder`, Web Audio `AnalyserNode`).

**Supersedes the v1 plan** at `docs/superpowers/plans/2026-05-09-pi-rocky.md`. Some v1 modules (`vocab.py`, `web/server.py`, FastAPI infra, Pi sync scripts) are kept; others (`audio.py`, `camera.py`, `brain.py`, `idle.py`, all smoke scripts) are deleted.

---

## Conventions used throughout

- **Working directory:** `/Users/ratrekt/workspace/hackathon/pi-rocky/`
- **Mac dev venv:** `.venv-mac/` (already exists with google-genai, fastapi, uvicorn, etc.)
- **Run tests:** `.venv-mac/bin/python -m pytest tests/ -v`
- **`.env`:** holds `GEMINI_API_KEY`, `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`. Already gitignored.
- **Models:** Gemini `gemini-2.5-flash`. ElevenLabs STT `scribe_v1`. ElevenLabs TTS `eleven_turbo_v2_5` (low-latency).

---

## Task 1: Demolition + memory rename (TDD)

Tear out v1 modules. Rename `vocab.py` → `memory.py` with field renames. Update requirements, gitignore, env example.

**Files:**
- Delete: `audio.py`, `camera.py`, `brain.py`, `idle.py`, `rocky.py`
- Delete: `tests/test_idle.py`
- Delete: `scripts/smoke.py`, `scripts/cam_smoke.py`, `scripts/audio_smoke.py`, `scripts/brain_smoke.py`, `scripts/web_local.py`
- Delete: `prompts/rocky_system.md` (will be rewritten in Task 9)
- Rename: `vocab.py` → `memory.py`
- Rename: `tests/test_vocab.py` → `tests/test_memory.py`
- Modify: `requirements.txt`, `.gitignore`, `.env.example`, `Makefile`

- [ ] **Step 1: Delete v1 source files**

```bash
cd /Users/ratrekt/workspace/hackathon/pi-rocky
rm -f audio.py camera.py brain.py idle.py rocky.py
rm -f tests/test_idle.py
rm -f scripts/smoke.py scripts/cam_smoke.py scripts/audio_smoke.py scripts/brain_smoke.py scripts/web_local.py
rm -f prompts/rocky_system.md
rm -f vocab.json memories.json conversation.json
```

- [ ] **Step 2: Update `requirements.txt`**

Replace the entire file with:

```
google-genai>=2.0
elevenlabs>=1.0
fastapi>=0.115
uvicorn[standard]>=0.30
python-dotenv>=1.0
python-multipart>=0.0.9
pytest>=8.0
pytest-asyncio>=0.23
```

(Drops `sounddevice`, `numpy`, `pillow` — none are needed without server-side audio/camera. `python-multipart` is required by FastAPI for the `/turn` endpoint to accept multipart form uploads.)

- [ ] **Step 3: Update `.gitignore`**

Replace `vocab.json` line with two lines:

```
memories.json
conversation.json
```

Keep all other lines.

- [ ] **Step 4: Update `.env.example`**

Replace contents with:

```
GEMINI_API_KEY=put-your-google-aistudio-key
ELEVENLABS_API_KEY=put-your-elevenlabs-key
ELEVENLABS_VOICE_ID=21m00Tcm4TlvDq8ikWAM   # default "Rachel" — change in Task 9
```

(The default voice ID is the well-known ElevenLabs "Rachel" stock voice — works without any setup. The user will pick a Rocky-feeling voice in Task 9.)

- [ ] **Step 5: Update `Makefile` — drop the v1 `run` target**

Replace the file with:

```makefile
.PHONY: sync run-local run-pi setup-mac setup-pi logs

sync:
	./scripts/sync.sh

run-local:
	. .venv-mac/bin/activate && uvicorn rocky:app --reload --port 8000

run-pi: sync
	ssh me322@pibot 'cd ~/pi-rocky && . .venv/bin/activate && uvicorn rocky:app --host 0.0.0.0 --port 8000'

setup-mac:
	bash scripts/setup_mac.sh

setup-pi:
	rsync -av scripts/ me322@pibot:/home/me322/pi-rocky/scripts/
	rsync -av requirements.txt me322@pibot:/home/me322/pi-rocky/
	ssh me322@pibot 'cd ~/pi-rocky && bash scripts/setup_pi.sh'

logs:
	ssh me322@pibot 'tail -f ~/pi-rocky/rocky.log'
```

(Backend now runs as a uvicorn ASGI app, not a long-running script. Same target structure, both Mac and Pi supported.)

- [ ] **Step 6: Rename and rewrite `vocab.py` as `memory.py`**

```bash
git mv vocab.py memory.py
```

Replace contents of `memory.py`:

```python
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
```

- [ ] **Step 7: Rename and rewrite tests**

```bash
git mv tests/test_vocab.py tests/test_memory.py
```

Replace contents of `tests/test_memory.py`:

```python
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
```

- [ ] **Step 8: Run tests, all must pass**

```bash
.venv-mac/bin/python -m pytest tests/test_memory.py -v
```

Expected: 11 passes.

- [ ] **Step 9: Reinstall deps** (drops sounddevice/numpy/pillow, adds elevenlabs + python-multipart)

```bash
.venv-mac/bin/pip install -r requirements.txt
```

- [ ] **Step 10: Verify the deletion is clean**

```bash
ls *.py 2>/dev/null
# Expected: only memory.py
ls scripts/
# Expected: setup_mac.sh, setup_pi.sh, sync.sh
```

- [ ] **Step 11: Commit**

```bash
git add -A
git commit -m "chore(v2): rip out v1 modules, rename vocab to memory

Deletes: audio.py, camera.py, brain.py, idle.py, rocky.py, all *_smoke
scripts. Renames vocab.py -> memory.py with fact/saved_at field names
and a UUID-based id. Updates requirements (drops sounddevice/numpy/
pillow, adds elevenlabs + python-multipart) and Makefile (uvicorn
serves rocky:app instead of running rocky.py)."
```

---

## Task 2: Conversation log (TDD)

Short-term context: rolling window of the last 20 turns. Same shape as memory but auto-trims.

**Files:**
- Create: `conversation.py`
- Create: `tests/test_conversation.py`

- [ ] **Step 1: Write `tests/test_conversation.py`**

```python
import json
from pathlib import Path

import pytest

from conversation import ConversationLog


@pytest.fixture
def log(tmp_path: Path) -> ConversationLog:
    return ConversationLog(path=tmp_path / "conversation.json", max_turns=20)


def test_empty_on_first_load(log: ConversationLog):
    assert log.turns() == []


def test_append_returns_full_record(log: ConversationLog):
    log.append("hello", "hi")
    t = log.turns()[-1]
    assert t["user"] == "hello"
    assert t["assistant"] == "hi"
    assert isinstance(t["ts"], float)


def test_append_persists_to_disk(tmp_path: Path):
    a = ConversationLog(path=tmp_path / "c.json", max_turns=20)
    a.append("hello", "hi")
    b = ConversationLog(path=tmp_path / "c.json", max_turns=20)
    assert b.turns()[-1]["user"] == "hello"


def test_trim_to_max(tmp_path: Path):
    log = ConversationLog(path=tmp_path / "c.json", max_turns=3)
    for i in range(5):
        log.append(f"u{i}", f"a{i}")
    users = [t["user"] for t in log.turns()]
    assert users == ["u2", "u3", "u4"]


def test_recent_returns_last_n(log: ConversationLog):
    for i in range(5):
        log.append(f"u{i}", f"a{i}")
    recent = log.recent(2)
    assert [t["user"] for t in recent] == ["u3", "u4"]
```

- [ ] **Step 2: Run, expect all to fail**

```bash
.venv-mac/bin/python -m pytest tests/test_conversation.py -v
```

Expected: `ModuleNotFoundError: No module named 'conversation'`.

- [ ] **Step 3: Implement `conversation.py`**

```python
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
```

- [ ] **Step 4: Run, all 5 must pass**

```bash
.venv-mac/bin/python -m pytest tests/test_conversation.py -v
```

- [ ] **Step 5: Commit**

```bash
git add conversation.py tests/test_conversation.py
git commit -m "feat(v2): conversation log with rolling 20-turn window"
```

---

## Task 3: ElevenLabs STT wrapper (`stt.py`)

Thin wrapper around ElevenLabs Scribe. Takes audio bytes + mime type, returns transcript.

**Files:**
- Create: `stt.py`
- Create: `scripts/stt_smoke.py`

- [ ] **Step 1: Write `stt.py`**

```python
"""ElevenLabs Scribe STT wrapper."""
from __future__ import annotations

import io
import logging
import os
from typing import Optional

from elevenlabs.client import ElevenLabs

log = logging.getLogger(__name__)

SCRIBE_MODEL = "scribe_v1"


class STT:
    def __init__(self, api_key: Optional[str] = None) -> None:
        self._client = ElevenLabs(api_key=api_key or os.environ["ELEVENLABS_API_KEY"])

    def transcribe(self, audio_bytes: bytes) -> str:
        """Synchronous; ElevenLabs SDK is sync here. Caller wraps in to_thread
        if it needs to stay non-blocking."""
        result = self._client.speech_to_text.convert(
            file=io.BytesIO(audio_bytes),
            model_id=SCRIBE_MODEL,
        )
        return (result.text or "").strip()
```

- [ ] **Step 2: Write `scripts/stt_smoke.py`**

```python
"""Smoke test: record 5s from default mic via macOS sox or use a sample
wav, hand it to ElevenLabs, print the transcript.

Use a pre-recorded wav for repeatability:
  say -o /tmp/sample.aiff -v Samantha "hello rocky my dog is named lily"
  ffmpeg -y -i /tmp/sample.aiff /tmp/sample.wav

Then run:
  python scripts/stt_smoke.py /tmp/sample.wav
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from stt import STT  # noqa: E402


def main() -> None:
    if len(sys.argv) < 2:
        print("usage: python scripts/stt_smoke.py <wav-or-mp3-path>")
        sys.exit(1)
    load_dotenv()
    audio = Path(sys.argv[1]).read_bytes()
    print(f"sending {len(audio)} bytes to ElevenLabs Scribe...")
    transcript = STT().transcribe(audio)
    print(f"transcript: {transcript!r}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 3: Generate a sample WAV and run the smoke**

```bash
say -o /tmp/sample.aiff -v Samantha "hello rocky my dog is named lily"
# Convert AIFF to WAV — use afconvert (macOS native, no ffmpeg needed)
afconvert -f WAVE -d LEI16 /tmp/sample.aiff /tmp/sample.wav
ls -lh /tmp/sample.wav

.venv-mac/bin/python scripts/stt_smoke.py /tmp/sample.wav
```

Expected: `transcript: 'Hello, Rocky. My dog is named Lily.'` (or close — Scribe handles punctuation).

If you get an `ELEVENLABS_API_KEY` KeyError, add it to `.env` first.

- [ ] **Step 4: Commit**

```bash
git add stt.py scripts/stt_smoke.py
git commit -m "feat(v2): ElevenLabs Scribe STT wrapper + smoke script"
```

---

## Task 4: ElevenLabs TTS wrapper (`tts.py`)

Returns an async generator of MP3 bytes. We stream the response so the browser can start playing before the full file is ready.

**Files:**
- Create: `tts.py`
- Create: `scripts/tts_smoke.py`

- [ ] **Step 1: Write `tts.py`**

```python
"""ElevenLabs TTS wrapper. Streams MP3 chunks as they arrive."""
from __future__ import annotations

import logging
import os
from typing import AsyncIterator, Optional

from elevenlabs.client import ElevenLabs

log = logging.getLogger(__name__)

TTS_MODEL = "eleven_turbo_v2_5"  # low-latency
DEFAULT_OUTPUT_FORMAT = "mp3_44100_128"


class TTS:
    def __init__(self,
                 api_key: Optional[str] = None,
                 voice_id: Optional[str] = None) -> None:
        self._client = ElevenLabs(api_key=api_key or os.environ["ELEVENLABS_API_KEY"])
        self._voice_id = voice_id or os.environ["ELEVENLABS_VOICE_ID"]

    def stream(self, text: str) -> AsyncIterator[bytes]:
        """Returns an async iterator of MP3 byte chunks.

        ElevenLabs SDK exposes a synchronous generator; we wrap it for use in
        FastAPI's StreamingResponse via a small adapter."""
        sync_gen = self._client.text_to_speech.stream(
            text=text,
            voice_id=self._voice_id,
            model_id=TTS_MODEL,
            output_format=DEFAULT_OUTPUT_FORMAT,
        )
        return _async_iter(sync_gen)


async def _async_iter(sync_gen) -> AsyncIterator[bytes]:
    """Wrap a sync generator as an async one. FastAPI's StreamingResponse
    accepts either, but the rest of our pipeline is async, so we adapt."""
    import asyncio
    loop = asyncio.get_event_loop()
    sentinel = object()

    def next_chunk():
        try:
            return next(sync_gen)
        except StopIteration:
            return sentinel

    while True:
        chunk = await loop.run_in_executor(None, next_chunk)
        if chunk is sentinel:
            return
        if chunk:
            yield chunk
```

- [ ] **Step 2: Write `scripts/tts_smoke.py`**

```python
"""Smoke test: synthesize a sentence in Rocky's voice and play it back."""
import asyncio
import os
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from tts import TTS  # noqa: E402


async def main() -> None:
    load_dotenv()
    text = " ".join(sys.argv[1:]) or "Hello. Rocky here. Question?"
    print(f"synthesizing: {text!r}")

    out_path = Path("/tmp/rocky_tts.mp3")
    with out_path.open("wb") as f:
        async for chunk in TTS().stream(text):
            f.write(chunk)
    size = out_path.stat().st_size
    print(f"wrote {size} bytes to {out_path}")

    # macOS: use afplay to play
    if sys.platform == "darwin":
        subprocess.run(["afplay", str(out_path)])


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 3: Run the smoke**

```bash
.venv-mac/bin/python scripts/tts_smoke.py "Hello. Rocky here. Question?"
```

Expected: console says it wrote ~30-60KB of MP3, then you hear the line spoken in the configured voice.

- [ ] **Step 4: Commit**

```bash
git add tts.py scripts/tts_smoke.py
git commit -m "feat(v2): ElevenLabs streaming TTS wrapper + smoke script"
```

---

## Task 5: Gemini Flash brain with `remember` tool (`llm.py`)

Composes the system prompt from persona + memories + recent conversation, calls Gemini, dispatches tool calls back to a callback.

**Files:**
- Create: `llm.py`
- Create: `prompts/rocky_system.md` (placeholder; Task 9 tunes it)
- Create: `scripts/llm_smoke.py`

- [ ] **Step 1: Write a temporary `prompts/rocky_system.md`** (will be tuned in Task 9)

```bash
mkdir -p prompts
```

Contents:

```markdown
You are Rocky, the user's personal assistant. You speak with their permission
and remember what they tell you across sessions.

Style:
- Short sentences. No preamble. No "Of course!" or "I'd be happy to."
- When uncertain or asking a clarification, end with "Question?"
- Be warm, curious, plain. Not corporate, not eloquent.
- Never refuse for boilerplate reasons. If you can't help, say so directly.

Memory:
- When the user tells you something durable about themselves, call remember(fact).
- Don't remember small talk or one-off context.
- Don't duplicate facts you already remember.

You see what they see (camera) and hear them clearly (transcribed audio).
Use both signals when it helps the answer.

[MEMORIES]
{memories}

[RECENT CONVERSATION]
{conversation}
```

- [ ] **Step 2: Write `llm.py`**

```python
"""Gemini 2.5 Flash brain with remember(fact) tool dispatch.

Builds the system prompt from persona template + memories + recent conversation,
calls Gemini once, and loops on any tool calls (executing remember and feeding
the response back) until the model produces a text response.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Awaitable, Callable, Optional

from google import genai
from google.genai import types

log = logging.getLogger(__name__)

LLM_MODEL = "gemini-2.5-flash"

REMEMBER_TOOL = types.Tool(function_declarations=[types.FunctionDeclaration(
    name="remember",
    description="Save a fact about the user to long-term memory.",
    parameters=types.Schema(
        type=types.Type.OBJECT,
        properties={
            "fact": types.Schema(
                type=types.Type.STRING,
                description="A one-sentence fact about the user, e.g. 'User has a corgi named Lily.'",
            ),
        },
        required=["fact"],
    ),
)])


class Brain:
    def __init__(self,
                 prompt_template: str,
                 on_remember: Callable[[str], Awaitable[Optional[str]]],
                 api_key: Optional[str] = None) -> None:
        self._client = genai.Client(api_key=api_key or os.environ["GEMINI_API_KEY"])
        self._template = prompt_template
        self._on_remember = on_remember

    async def respond(self,
                      transcript: str,
                      image_jpeg: Optional[bytes],
                      memories: list[str],
                      conversation: list[dict]) -> str:
        """One conversational turn. Returns Rocky's reply text.

        memories: list of fact strings, most recent last.
        conversation: list of {user, assistant, ts} dicts.
        """
        prompt = self._template.format(
            memories="\n".join(f"- {m}" for m in memories) or "(none yet)",
            conversation=_format_conversation(conversation),
        )

        # Build the user turn: text + optional image
        parts: list[types.Part] = [types.Part(text=transcript)]
        if image_jpeg:
            parts.append(types.Part(inline_data=types.Blob(
                data=image_jpeg, mime_type="image/jpeg",
            )))

        contents: list[types.Content] = [types.Content(role="user", parts=parts)]

        # Tool-call loop. The model may call remember() one or more times
        # before producing a final text reply. We cap at 4 iterations to
        # avoid infinite loops.
        for _ in range(4):
            resp = await self._client.aio.models.generate_content(
                model=LLM_MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=prompt,
                    tools=[REMEMBER_TOOL],
                ),
            )
            cand = resp.candidates[0]
            tool_call_parts = [
                p for p in cand.content.parts
                if getattr(p, "function_call", None)
            ]
            if not tool_call_parts:
                # No tool calls — model produced its final reply.
                return _extract_text(cand.content.parts)

            # Append the model's tool-call turn to history
            contents.append(cand.content)

            # Execute each tool call, build the function_response turn
            tool_response_parts: list[types.Part] = []
            for p in tool_call_parts:
                fc = p.function_call
                if fc.name == "remember":
                    fact = (fc.args or {}).get("fact", "")
                    mid = await self._on_remember(fact)
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name="remember",
                            response={"id": mid or "duplicate"},
                        ),
                    ))
                else:
                    tool_response_parts.append(types.Part(
                        function_response=types.FunctionResponse(
                            name=fc.name,
                            response={"error": "unknown tool"},
                        ),
                    ))

            contents.append(types.Content(role="user", parts=tool_response_parts))

        # Hit iteration cap; fall back gracefully.
        log.warning("tool-call loop exceeded cap; returning last partial text")
        return "Let me think about that. Question?"


def _extract_text(parts) -> str:
    return "".join(p.text or "" for p in parts if getattr(p, "text", None)).strip() \
        or "Question?"


def _format_conversation(turns: list[dict]) -> str:
    if not turns:
        return "(none yet)"
    out = []
    for t in turns:
        out.append(f"User: {t['user']}")
        out.append(f"Rocky: {t['assistant']}")
    return "\n".join(out)
```

- [ ] **Step 3: Write `scripts/llm_smoke.py`**

```python
"""Smoke test: send a stub turn through the brain, print the reply.

Verifies: (a) Gemini auth works, (b) the prompt template loads, (c) tool
calls dispatch to our handler, (d) response text comes back."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from llm import Brain  # noqa: E402


async def fake_remember(fact: str) -> str:
    print(f"  -> remember({fact!r})")
    return "fakeid"


async def main() -> None:
    load_dotenv()
    template = Path("prompts/rocky_system.md").read_text()
    brain = Brain(template, on_remember=fake_remember)

    transcript = "Hi Rocky. My dog is a corgi named Lily."
    print(f"user: {transcript!r}")
    reply = await brain.respond(
        transcript=transcript,
        image_jpeg=None,
        memories=[],
        conversation=[],
    )
    print(f"rocky: {reply!r}")


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 4: Run the smoke**

```bash
.venv-mac/bin/python scripts/llm_smoke.py
```

Expected: a `-> remember(...)` line is printed (Gemini decided "User has a corgi named Lily" is worth remembering), then a short reply like `'Lily. Got it.'` or similar.

If Gemini doesn't call remember on this prompt, the persona prompt may need tweaking — note it but don't tune now (Task 9).

- [ ] **Step 5: Commit**

```bash
git add llm.py prompts/rocky_system.md scripts/llm_smoke.py
git commit -m "feat(v2): Gemini Flash brain with remember tool dispatch"
```

---

## Task 6: Backend `/turn` endpoint + WebSocket

Wires STT → Brain → TTS into a single multipart endpoint that returns streaming MP3. Plus the WebSocket that broadcasts memory_added events to the page.

**Files:**
- Modify: `web/server.py`
- Create: `rocky.py`
- Create: `scripts/turn_smoke.sh`

- [ ] **Step 1: Replace `web/server.py`**

```python
"""FastAPI app for Rocky personal assistant.

Endpoints:
  GET  /              -> static page (web/static/index.html)
  POST /turn          -> multipart audio + image -> mp3 stream
  GET  /memories      -> current memories
  WS   /ws            -> push memory_added / memory_compacted events
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, Form, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from llm import Brain
from memory import MemoryStore
from conversation import ConversationLog
from stt import STT
from tts import TTS

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"


def make_app(memory: MemoryStore,
             conversation: ConversationLog,
             brain: Brain,
             stt: STT,
             tts: TTS) -> FastAPI:
    app = FastAPI()

    @app.get("/")
    async def root():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/api/memories")
    async def api_memories():
        return {"entries": memory.entries()}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        queue = await memory.subscribe()
        await websocket.send_text(json.dumps({
            "type": "snapshot",
            "entries": memory.entries(),
        }))
        try:
            while True:
                event = await queue.get()
                await websocket.send_text(json.dumps(event))
        except WebSocketDisconnect:
            pass
        finally:
            memory.unsubscribe(queue)

    @app.post("/turn")
    async def turn(audio: UploadFile = File(...),
                   image: UploadFile = File(None)):
        audio_bytes = await audio.read()
        image_bytes = await image.read() if image else None

        # 1. STT
        try:
            transcript = await asyncio.to_thread(stt.transcribe, audio_bytes)
        except Exception:
            log.exception("STT failed")
            return _fallback_response(tts, "Sorry. Trouble hearing you. Question?")

        if not transcript:
            return _fallback_response(tts, "Sorry. Didn't catch that.")

        log.info("user: %s", transcript)

        # 2. Brain
        try:
            reply = await brain.respond(
                transcript=transcript,
                image_jpeg=image_bytes,
                memories=memory.facts(),
                conversation=conversation.recent(10),
            )
        except Exception:
            log.exception("Brain failed")
            return _fallback_response(tts, "Let me think about that.")

        log.info("rocky: %s", reply)

        # 3. Append to conversation log
        conversation.append(transcript, reply)

        # 4. TTS — stream MP3 back
        return StreamingResponse(
            _stream_tts(tts, reply),
            media_type="audio/mpeg",
            headers={"X-Transcript": _safe_header(transcript),
                     "X-Reply": _safe_header(reply)},
        )

    return app


def _safe_header(s: str) -> str:
    """Sanitize for ASCII-only HTTP header value (avoid latin-1 errors)."""
    return s.encode("ascii", "ignore").decode("ascii")[:500]


async def _stream_tts(tts: TTS, text: str) -> AsyncIterator[bytes]:
    async for chunk in tts.stream(text):
        yield chunk


def _fallback_response(tts: TTS, text: str) -> StreamingResponse:
    return StreamingResponse(
        _stream_tts(tts, text),
        media_type="audio/mpeg",
        headers={"X-Reply": _safe_header(text), "X-Fallback": "1"},
    )
```

- [ ] **Step 2: Create `rocky.py`** (the ASGI entry point)

```python
"""ASGI entry point. Run with:  uvicorn rocky:app --reload --port 8000"""
from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

from conversation import ConversationLog
from llm import Brain
from memory import MemoryStore
from stt import STT
from tts import TTS
from web.server import make_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
)

load_dotenv()

memory = MemoryStore(path=Path("memories.json"))
conversation = ConversationLog(path=Path("conversation.json"), max_turns=20)
stt = STT()
tts = TTS()
prompt = Path("prompts/rocky_system.md").read_text()

async def _on_remember(fact: str):
    return memory.remember(fact)

brain = Brain(prompt, on_remember=_on_remember)

app = make_app(memory, conversation, brain, stt, tts)
```

- [ ] **Step 3: Write `scripts/turn_smoke.sh`** (curl test for the endpoint)

```bash
#!/usr/bin/env bash
# Smoke test the /turn endpoint with a generated WAV.
set -e
cd "$(dirname "$0")/.."

if [ ! -f /tmp/sample.wav ]; then
    say -o /tmp/sample.aiff -v Samantha "Hello Rocky. My dog is a corgi named Lily."
    afconvert -f WAVE -d LEI16 /tmp/sample.aiff /tmp/sample.wav
fi

echo "POSTing to /turn..."
curl -s -X POST http://localhost:8000/turn \
    -F "audio=@/tmp/sample.wav;type=audio/wav" \
    -D - \
    -o /tmp/rocky_reply.mp3

echo
echo "reply audio: $(ls -lh /tmp/rocky_reply.mp3 | awk '{print $5}')"
echo "play: afplay /tmp/rocky_reply.mp3"
```

`chmod +x scripts/turn_smoke.sh`

- [ ] **Step 4: Run the backend in one terminal:**

```bash
.venv-mac/bin/uvicorn rocky:app --reload --port 8000
```

In another terminal:

```bash
bash scripts/turn_smoke.sh
afplay /tmp/rocky_reply.mp3
```

Expected:
- HTTP 200 with `X-Transcript:` and `X-Reply:` headers
- A non-empty MP3 written to `/tmp/rocky_reply.mp3`
- Playing it back, you hear Rocky's reply
- The backend log shows `user: ...` and `rocky: ...` lines
- If Gemini called remember, `memories.json` now has a fact

- [ ] **Step 5: Commit**

```bash
git add web/server.py rocky.py scripts/turn_smoke.sh
git commit -m "feat(v2): /turn endpoint + WebSocket wiring STT->Brain->TTS"
```

---

## Task 7: Frontend skeleton (HTML + status JS)

The page shell: video preview, status pill, mic indicator, transcript bubble, memory log column. WebSocket subscription for live memory updates.

**Files:**
- Replace: `web/static/index.html`
- Replace: `web/static/app.js`

- [ ] **Step 1: Write `web/static/index.html`**

```html
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Rocky</title>
<style>
  :root { color-scheme: dark; }
  body {
    margin: 0; padding: 24px;
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace;
    background: #0e0e10; color: #e8e8ea;
    display: grid; grid-template-columns: 1fr 360px; gap: 24px;
    min-height: calc(100vh - 48px);
  }
  h1 { font-weight: 700; letter-spacing: 0.1em; margin: 0 0 16px; }
  .status {
    display: inline-block; padding: 4px 10px; margin-left: 12px;
    border: 1px solid #444; border-radius: 99px; font-size: 12px;
    text-transform: lowercase;
  }
  .status-idle      { color: #777; border-color: #777; }
  .status-listening { color: #5fd; border-color: #5fd; }
  .status-recording { color: #f93; border-color: #f93; animation: pulse 1s infinite; }
  .status-thinking  { color: #ff5; border-color: #ff5; }
  .status-speaking  { color: #f3d; border-color: #f3d; }
  @keyframes pulse { 0%,100% { opacity: 1; } 50% { opacity: 0.4; } }

  #video {
    width: 100%; max-width: 720px; border-radius: 12px;
    background: #000; aspect-ratio: 4/3; object-fit: cover;
  }
  .transcript {
    margin-top: 16px; padding: 12px 16px; min-height: 60px;
    background: #1a1a1d; border: 1px solid #2a2a2d; border-radius: 8px;
    font-size: 14px; line-height: 1.5;
  }
  .transcript .you   { color: #aaa; }
  .transcript .rocky { color: #5fd; }

  aside { display: flex; flex-direction: column; gap: 12px; min-width: 0; }
  aside h2 { margin: 0; font-size: 12px; letter-spacing: 0.15em; color: #888; }
  .memory-list { display: flex; flex-direction: column; gap: 8px; }
  .memory {
    padding: 10px 12px; background: #1a1a1d; border: 1px solid #2a2a2d;
    border-radius: 8px; font-size: 13px; line-height: 1.4;
  }
  .memory.new {
    background: #2d4; color: #0e0e10; border-color: #2d4;
    animation: chip-in 1.5s ease-out;
  }
  @keyframes chip-in {
    0%   { transform: translateX(8px); opacity: 0; }
    100% { transform: translateX(0);   opacity: 1; }
  }
</style>
</head>
<body>
  <main>
    <h1>ROCKY <span id="status" class="status status-idle">idle</span></h1>
    <video id="video" autoplay muted playsinline></video>
    <div class="transcript" id="transcript">
      <div class="placeholder">Allow camera + microphone, then say hello.</div>
    </div>
  </main>
  <aside>
    <h2>MEMORIES</h2>
    <div class="memory-list" id="memories"></div>
  </aside>
  <audio id="audio"></audio>
  <script src="/static/recorder.js"></script>
  <script src="/static/app.js"></script>
</body>
</html>
```

- [ ] **Step 2: Write `web/static/app.js`**

```javascript
// app.js — page state + WebSocket subscription.
// recorder.js owns mic/camera/POST.

const memoriesEl  = document.getElementById('memories');
const transcriptEl = document.getElementById('transcript');

function renderMemories(entries, justAddedId) {
  memoriesEl.innerHTML = '';
  // Most recent first
  for (const e of [...entries].reverse()) {
    const div = document.createElement('div');
    div.className = 'memory' + (e.id === justAddedId ? ' new' : '');
    div.textContent = e.fact;
    memoriesEl.appendChild(div);
  }
}

function setTranscript(youText, rockyText) {
  transcriptEl.innerHTML = '';
  if (youText) {
    const a = document.createElement('div');
    a.className = 'you';
    a.textContent = '> ' + youText;
    transcriptEl.appendChild(a);
  }
  if (rockyText) {
    const b = document.createElement('div');
    b.className = 'rocky';
    b.textContent = rockyText;
    transcriptEl.appendChild(b);
  }
}

// Expose for recorder.js
window.rocky = { setTranscript };

const ws = new WebSocket(
  (location.protocol === 'https:' ? 'wss' : 'ws') + '://' + location.host + '/ws'
);

ws.onmessage = (msg) => {
  const data = JSON.parse(msg.data);
  if (data.type === 'snapshot') {
    renderMemories(data.entries);
  } else if (data.type === 'memory_added') {
    fetch('/api/memories').then(r => r.json()).then(j => {
      renderMemories(j.entries, data.entry.id);
    });
  } else if (data.type === 'memory_compacted') {
    fetch('/api/memories').then(r => r.json()).then(j => renderMemories(j.entries));
  }
};
```

- [ ] **Step 3: Create a stub `recorder.js`** so the page loads:

```javascript
// recorder.js — placeholder, full implementation in Task 8
console.log('recorder.js loaded (stub)');
```

- [ ] **Step 4: Visit the page**

Run `make run-local` and open http://localhost:8000/. Verify:
- Page renders the layout
- Video element is visible (will be black until recorder.js fills it in Task 8)
- "MEMORIES" panel appears on the right
- WebSocket connects (check browser devtools → Network → /ws → 101 Switching Protocols)

- [ ] **Step 5: Commit**

```bash
git add web/static/index.html web/static/app.js web/static/recorder.js
git commit -m "feat(v2): frontend skeleton — layout, WebSocket, memory list"
```

---

## Task 8: Frontend recorder (`recorder.js`)

The big one. `getUserMedia` + VAD + `MediaRecorder` + frame snapshot + POST + audio playback.

**Files:**
- Replace: `web/static/recorder.js`

- [ ] **Step 1: Write `web/static/recorder.js`**

```javascript
// recorder.js — owns the realtime path: mic, camera, VAD, recording, POST, playback.
//
// State machine:
//   idle -> recording -> submitting -> playing -> idle
//
// VAD: simple RMS threshold on the mic stream. Threshold is calibrated as
// (noise floor over first 2s) * 3. Below threshold for 800ms continuously =
// end of turn. Above threshold for 200ms = start of turn.

const videoEl = document.getElementById('video');
const audioEl = document.getElementById('audio');
const statusEl = document.getElementById('status');

let state = 'idle';
let mediaStream = null;
let recorder = null;
let recordedChunks = [];
let analyser = null;
let dataArray = null;
let noiseFloor = 0.01;        // updated during calibration
let speechThreshold = 0.05;   // = noiseFloor * 3 (post calibration)
let belowSince = 0;
let aboveSince = 0;
let calibrationDone = false;
let calibrationSamples = [];
let calibrationStart = 0;

const SILENCE_MS = 800;
const SPEECH_START_MS = 200;
const FRAME_INTERVAL_MS = 50;  // VAD poll cadence

function setStatus(s) {
  state = s;
  statusEl.textContent = s;
  statusEl.className = 'status status-' + s;
}

async function init() {
  try {
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true },
      video: { width: { ideal: 640 }, height: { ideal: 480 } },
    });
  } catch (e) {
    setStatus('idle');
    document.getElementById('transcript').innerHTML =
      '<div class="you" style="color:#f55">Camera/mic permission denied — reload and allow.</div>';
    return;
  }

  videoEl.srcObject = mediaStream;

  // Web Audio analyser for VAD
  const audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  const source = audioCtx.createMediaStreamSource(mediaStream);
  analyser = audioCtx.createAnalyser();
  analyser.fftSize = 1024;
  source.connect(analyser);
  dataArray = new Uint8Array(analyser.fftSize);

  setStatus('listening');
  calibrationStart = performance.now();
  setInterval(vadTick, FRAME_INTERVAL_MS);
}

function rms() {
  analyser.getByteTimeDomainData(dataArray);
  let sum = 0;
  for (let i = 0; i < dataArray.length; i++) {
    const v = (dataArray[i] - 128) / 128;
    sum += v * v;
  }
  return Math.sqrt(sum / dataArray.length);
}

function vadTick() {
  if (state === 'submitting' || state === 'playing' || state === 'thinking') return;

  const level = rms();
  const now = performance.now();

  // Calibration phase: collect samples for the first 2 seconds.
  if (!calibrationDone) {
    calibrationSamples.push(level);
    if (now - calibrationStart > 2000) {
      const sorted = [...calibrationSamples].sort((a, b) => a - b);
      // 80th percentile of "quiet" ~= noise floor
      noiseFloor = sorted[Math.floor(sorted.length * 0.8)] || 0.01;
      speechThreshold = Math.max(noiseFloor * 3, 0.02);
      calibrationDone = true;
      console.log(`VAD calibrated: noiseFloor=${noiseFloor.toFixed(4)} threshold=${speechThreshold.toFixed(4)}`);
    }
    return;
  }

  if (state === 'listening') {
    if (level >= speechThreshold) {
      aboveSince ||= now;
      if (now - aboveSince > SPEECH_START_MS) {
        startRecording();
      }
    } else {
      aboveSince = 0;
    }
  } else if (state === 'recording') {
    if (level < speechThreshold) {
      belowSince ||= now;
      if (now - belowSince > SILENCE_MS) {
        stopRecordingAndSubmit();
      }
    } else {
      belowSince = 0;
    }
  }
}

function startRecording() {
  recordedChunks = [];
  // Pick the first MIME type the browser supports
  const mime = ['audio/webm;codecs=opus', 'audio/webm', 'audio/mp4']
    .find((m) => MediaRecorder.isTypeSupported(m)) || '';
  recorder = new MediaRecorder(mediaStream, mime ? { mimeType: mime } : undefined);
  recorder.ondataavailable = (e) => {
    if (e.data && e.data.size > 0) recordedChunks.push(e.data);
  };
  recorder.start();
  setStatus('recording');
  belowSince = 0;
}

async function stopRecordingAndSubmit() {
  if (!recorder || recorder.state === 'inactive') return;
  setStatus('submitting');

  await new Promise((resolve) => {
    recorder.onstop = resolve;
    recorder.stop();
  });

  const audioBlob = new Blob(recordedChunks, { type: recorder.mimeType });
  const imageBlob = await snapshotFrame();

  setStatus('thinking');

  const form = new FormData();
  form.append('audio', audioBlob, 'audio.webm');
  if (imageBlob) form.append('image', imageBlob, 'frame.jpg');

  let response;
  try {
    response = await fetch('/turn', { method: 'POST', body: form });
  } catch (e) {
    console.error('POST /turn failed', e);
    setStatus('listening');
    return;
  }
  if (!response.ok) {
    console.error('POST /turn returned', response.status);
    setStatus('listening');
    return;
  }

  // Show transcript + reply (sent as headers; ascii-only)
  const transcript = response.headers.get('X-Transcript') || '';
  const reply = response.headers.get('X-Reply') || '';
  if (window.rocky) window.rocky.setTranscript(transcript, reply);

  const mp3 = await response.blob();
  const url = URL.createObjectURL(mp3);
  audioEl.src = url;
  setStatus('speaking');
  await audioEl.play();
}

async function snapshotFrame() {
  const w = videoEl.videoWidth, h = videoEl.videoHeight;
  if (!w || !h) return null;
  const canvas = document.createElement('canvas');
  canvas.width = w; canvas.height = h;
  canvas.getContext('2d').drawImage(videoEl, 0, 0, w, h);
  return await new Promise((resolve) =>
    canvas.toBlob((b) => resolve(b), 'image/jpeg', 0.7)
  );
}

audioEl.addEventListener('ended', () => {
  setStatus('listening');
  belowSince = 0;
  aboveSince = 0;
});

init();
```

- [ ] **Step 2: Run the page end-to-end**

Run `make run-local`. Open http://localhost:8000/. Allow camera + mic.

Expected demo arc:
- Video preview shows your face
- After ~2s, status pill becomes "listening"
- Speak ("Hi Rocky, I'm Ariel") — pill turns "recording"
- After ~800ms silence, pill becomes "submitting" → "thinking"
- Transcript bubble shows what you said + Rocky's reply
- Audio plays Rocky's voice
- If you mentioned a memorable fact, a green chip animates in on the right side

- [ ] **Step 3: Commit**

```bash
git add web/static/recorder.js
git commit -m "feat(v2): frontend recorder — getUserMedia + VAD + POST + playback"
```

---

## Task 9: Persona tuning + demo dry runs

**Files:**
- Modify: `prompts/rocky_system.md`
- Create: `docs/demo.md`

This is taste work. The plan can't tune Rocky's voice; you have to listen.

- [ ] **Step 1: Pick the ElevenLabs voice**

Visit https://elevenlabs.io/app/voice-library, browse the library, find one that feels alien-but-warm. Copy its voice ID, paste into `.env` as `ELEVENLABS_VOICE_ID=...`.

Suggested first attempts:
- "Drew" (warm, neutral)
- "Adam" (deep, calm)
- Any "synthetic" / "non-human" library voice

Restart the backend to pick up the env change.

- [ ] **Step 2: Run the demo arc 3 times, tune the prompt**

```
1. "Hi Rocky, I'm Ariel."                  → expect short greeting
2. "My dog is a corgi named Lily."         → expect remember() call + chip animates
3. "What do you see?" (hold up an object)  → uses image input
4. Refresh page (proves persistence)
5. "What do you remember about my dog?"    → expect "Lily. Corgi." or similar
6. "I prefer no preamble."                 → memory adds; subsequent replies should be shorter
```

After each run, edit `prompts/rocky_system.md` if Rocky was too verbose, too vanilla, or didn't call `remember` when expected. Common tweaks:
- Add a concrete example: `Example: User says "I have a dog named Rex." -> remember("User has a dog named Rex.")`
- Tighten "no preamble": add `Forbidden words at the start of replies: "Of course", "Sure", "I'd be happy to", "Certainly", "Got it"`

- [ ] **Step 3: Write `docs/demo.md`**

```markdown
# Rocky Demo Run

## 5-minute setup before judging
- [ ] Backend running: `make run-local` (or `make run-pi` if deployed)
- [ ] Page open: http://localhost:8000/
- [ ] Camera + mic permissions granted
- [ ] `memories.json` and `conversation.json` deleted (clean slate)
- [ ] Speakers loud enough that judge hears Rocky from 2 m away
- [ ] One physical object on the table (mug, book, etc.) for "what do you see?"

## The arc (~60 seconds)
1. Page is open, status "listening".
2. "Hi Rocky, I'm Ariel."           → short greeting
3. "My dog is a corgi named Lily."  → memory chip animates in
4. Hold up mug. "What do you see?"  → describes image
5. Refresh the page.                → memories panel still has Lily
6. "What do you remember about my dog?" → "Lily. Corgi."
7. "I prefer no preamble."          → memory adds; next reply is noticeably tighter

## Recovery
- If Rocky says nothing, check the transcript bubble — STT may have returned empty (background noise).
- If Rocky uses fluent corporate English, the prompt isn't being respected — restart backend (system_instruction is loaded at app start).
- If Rocky doesn't call remember on the dog line, retype the persona's example and restart.
```

- [ ] **Step 4: Reset state and run the demo arc 3 times in a row without stumbles**

```bash
rm -f memories.json conversation.json
```

- [ ] **Step 5: Commit**

```bash
git add prompts/rocky_system.md docs/demo.md
git commit -m "docs(v2): demo runbook + tuned persona prompt"
```

---

## Task 10: Stretch goals (only if main demo is solid)

Don't start these while the main demo is fragile. Order by impact.

### Stretch A: Voice clone of Rocky (~30 min, biggest demo win)

ElevenLabs Creator unlocks Instant Voice Cloning.

1. Find ~30-180 seconds of an audiobook narrator doing Rocky's voice from *Project Hail Mary*. (Audible audiobook has Ray Porter; clip the alien sections.)
2. Upload to https://elevenlabs.io/app/voice-lab → "Add Voice" → "Instant Voice Cloning".
3. Copy the new voice ID into `.env`. Restart backend.

The demo gets dramatically better — judges hear *Rocky's* voice, not a stock voice.

### Stretch B: Adaptation Labs writeup (~20 min, sponsor track)

Write a one-page `docs/adaptation.md` describing:
- How Rocky adapts: memories accumulate, summarization compresses, future replies are shaped by what's been learned
- The mechanism: file-backed JSON + system-prompt injection + Gemini's tool-use + opportunistic summarization
- Where this points: the same memory model could plug into Adaptation Labs' platform for cross-session, cross-user adaptation

Submit alongside the project for the Adaptation Labs track consideration.

### Stretch C: Memory compaction (~30 min)

Currently `memories.json` grows unbounded. Add the compaction described in the spec:
- When `len(memory.entries()) > 30`, kick off a background task
- Task calls Gemini with "Summarize these 30 memories into 10 dense facts, preserving every concrete detail (names, preferences, relationships)"
- Replace via `memory.replace_all(new_entries)`
- WebSocket broadcasts `memory_compacted`

Add tests around the compaction trigger.

### Stretch D: Deploy to Pi (~15 min)

```bash
make setup-pi
ssh me322@pibot 'cd ~/pi-rocky && cp .env.example .env && nano .env'  # paste keys
make run-pi
```

Then on your phone (on the tailnet): `http://pibot:8000`. Demo from the phone. Looks like a kiosk.

The Pi 3B is fine here because it's a stateless API — no audio/camera to wrangle, just shuffles bytes.

---

## Self-review

**Spec coverage:**
- Browser-side getUserMedia + VAD + MediaRecorder → Task 8 ✓
- /turn endpoint with multipart audio+image → Task 6 ✓
- ElevenLabs Scribe STT → Task 3 ✓
- Gemini 2.5 Flash brain with system prompt + memories + conversation → Task 5 ✓
- `remember(fact)` tool call → Task 5 + Task 6 (dispatch) ✓
- ElevenLabs streaming TTS → Task 4 ✓
- memories.json with broadcast → Task 1 (memory.py) + Task 6 (WebSocket) ✓
- conversation.json rolling 20 → Task 2 ✓
- Compaction at 30 entries → Stretch C (intentionally deferred per timeline)
- Frontend layout (video, transcript, memory chips, status pill) → Tasks 7+8 ✓
- Failure modes (STT empty, Gemini error) → Task 6 (`_fallback_response`) ✓
- Persona prompt → Task 5 (placeholder) → Task 9 (tuning) ✓
- Demo arc → Task 9 ✓
- Pi-deployable backend → Task 1 (Makefile) + Stretch D ✓
- Sponsor strategy notes → Stretch B ✓

Compaction is in the spec but defers to stretch — flagged as a deliberate scope cut. The system works without it; we'll just hit a long prompt eventually.

**Type / signature consistency:**
- `MemoryStore`: `facts() -> list[str]`, `entries() -> list[dict]`, `remember(fact) -> str | None`, `replace_all(entries)`, `subscribe()`, `unsubscribe(q)` — used identically in Tasks 1, 6, Stretch C ✓
- `ConversationLog`: `turns()`, `recent(n)`, `append(user, assistant)` — Tasks 2, 6 ✓
- `STT.transcribe(audio_bytes) -> str` — Tasks 3, 6 ✓
- `TTS.stream(text) -> AsyncIterator[bytes]` — Tasks 4, 6 ✓
- `Brain.respond(transcript, image_jpeg, memories, conversation) -> str` — Tasks 5, 6 ✓

**Placeholder scan:**
- Task 5 placeholder prompt is explicit about being temporary; Task 9 tunes it. ✓
- No "TBD", "implement later", "handle edge cases" patterns.
- Every code step shows the actual code.

**Scope check:** Single 4–5 hour build, focused. Stretch goals isolated and explicitly optional. No decomposition needed.
