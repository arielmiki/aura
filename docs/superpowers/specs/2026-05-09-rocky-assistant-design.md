# Rocky Personal Assistant — Design (v2)

**Date:** 2026-05-09
**Author:** ratrekt
**Event:** AI Engineer Singapore Hackathon (7 hours, solo)
**Supersedes:** [`2026-05-09-pi-rocky-design.md`](2026-05-09-pi-rocky-design.md) — v1 used Gemini Live + word-learning. Pivoted because Live streaming was unreliable and the alien-language gimmick was less useful than a real assistant.

## What we're building

A browser-based personal assistant with a "Rocky" persona (a name and a distinctive ElevenLabs voice). Camera and mic stay on the whole time the page is open. When the user finishes a sentence, the browser uploads the audio + a single video frame to a stateless backend, which runs ElevenLabs Scribe → Gemini 2.5 Flash → ElevenLabs TTS and returns audio. The assistant maintains a JSON-backed knowledge base of facts about the user; it gets smarter and more personalized as it accumulates memories.

The 60-second demo: walk up, talk to Rocky, watch a memory chip pop up when Rocky decides something is worth remembering, refresh the page, prove the memory survived.

The pitch hits the **ElevenLabs track** primarily (STT + TTS + a custom voice), the **Gemini track** secondarily, and is naturally extensible to the **Adaptation Labs track** (the assistant adapts its behavior based on accumulated memories).

## What changed from v1

- **Architecture:** Browser-side `getUserMedia` for mic + camera. Backend is a stateless request/response API. No server-side audio devices, no OpenCV pipeline. The lag we saw with v1 was the asyncio event loop being blocked by camera capture and JPEG encoding — that whole class of problem is now gone.
- **Pipeline:** Discrete STT → LLM → TTS instead of streaming Gemini Live. Slower per turn (~1–2 s end to end vs Live's <500 ms) but bulletproof.
- **Persona:** "Personal assistant with Rocky voice and accent" replaces "alien who learns English." Rocky's flavor shows up in the voice and in stylistic constraints (no preamble, short sentences, "Question?" at uncertainty).
- **Knowledge:** Adaptive memory replaces vocabulary list. The `learn_word` tool is gone; in its place is `remember(fact)`.

## System overview

```
┌─────────────────────────────────────────────────────────────────────┐
│  Browser   (Mac / phone / Pi-kiosk — anywhere with getUserMedia)    │
│                                                                     │
│   <video>  ◄── getUserMedia(camera)   live preview, frame is        │
│                                       snapshot only at end of turn  │
│   <mic>    ◄── getUserMedia(audio)                                  │
│             │                                                       │
│             ├─► VAD (RMS threshold + 800ms silence)                 │
│             │      │                                                │
│             ▼      ▼                                                │
│         MediaRecorder  ─►  WAV/Opus blob + JPEG snapshot            │
│                                  │                                  │
│                                  ▼  POST /turn                      │
│   <audio> ◄── audio response (mp3 stream)                           │
└──────────────────────────────────│──────────────────────────────────┘
                                   │
                                   ▼
┌─────────────────────────────────────────────────────────────────────┐
│  Backend   (FastAPI — runs on Mac, Pi, anywhere)                    │
│                                                                     │
│   POST /turn                                                        │
│     ├─► ElevenLabs Scribe (STT)        — transcript                 │
│     ├─► Gemini 2.5 Flash               — text + image input         │
│     │     system prompt = persona + memories + recent conversation  │
│     │     tool: remember(fact)                                      │
│     ├─► (if remember called) memory.append + WebSocket broadcast    │
│     ├─► ElevenLabs TTS                 — MP3 bytes                  │
│     └─► return MP3 streamed in HTTP response body                   │
│                                                                     │
│   GET /memories          — current memories.json                    │
│   WS  /ws                — push memory_added events                 │
│   GET /                  — static page                              │
└─────────────────────────────────────────────────────────────────────┘
```

**Key properties:**
- Browser owns the realtime path. Backend never touches an audio device or a webcam.
- Backend is stateless (per request) except for `memories.json` on disk.
- Same backend code runs on Mac for development, on a Pi for deployment, anywhere with Python 3.10+.

## Hardware

None required server-side. Browser uses any built-in or USB camera/mic via `getUserMedia`. macOS prompts for camera + microphone permission on first page load.

For Pi deployment: backend runs on Pi (Python 3.13 already set up via prior `setup_pi.sh`). The Pi's mic/cam are not used unless the Pi runs Chromium kiosk against itself, in which case Chromium's `getUserMedia` works against ALSA + V4L2 transparently.

## Turn lifecycle

Walk-through of one user turn.

### Browser state machine

```
idle ──speech detected──► recording
                              │
                              │  (RMS below threshold for 800ms)
                              ▼
                          submitting (uploading wav + frame)
                              │
                              ▼
                          waiting ──audio received──► playing
                                                          │
                                                          │ ended
                                                          ▼
                                                        idle
```

### Steps

1. **Always-on capture.** On page load, `getUserMedia({audio: true, video: true})` opens both streams once and holds them. `<video>` shows live preview locally — never sent to the server except as a snapshot at end of turn.

2. **VAD.** A Web Audio `AnalyserNode` watches RMS amplitude on the mic stream. When RMS crosses a noise-floor threshold (calibrated automatically over the first 2 s), state transitions to `recording`. Samples are written into a `MediaRecorder`.

3. **End-of-turn detection.** When RMS stays below threshold for **800 ms continuously**, state transitions to `submitting`. Stop the recorder, take a single frame from `<video>` via `<canvas>.drawImage` → JPEG blob.

4. **POST /turn** (multipart form):
   - `audio` — recorded blob (WebM/Opus, the browser's native MediaRecorder format)
   - `image` — JPEG snapshot
   - `session_id` — UUID generated once on page load, used for grouping turns

5. **Backend pipeline (~1–2 s end-to-end):**
   1. **STT** — POST audio to ElevenLabs Scribe → transcript. Empty / inaudible → return a pre-recorded "didn't catch that" clip, no Gemini call.
   2. **Brain** — call Gemini 2.5 Flash with system prompt (persona + memories + last 10 conversation turns) + user content (transcript + image). Tool: `remember(fact: str)`.
   3. **Tool dispatch** — for each `remember` call, append to `memories.json`, broadcast `{"type": "memory_added", "fact": ...}` over `/ws`.
   4. **Append to conversation log** — push `{user_turn, assistant_turn, ts}` onto `conversation.json`, trim to last 20.
   5. **TTS** — POST response text to ElevenLabs TTS → MP3 stream → return as `audio/mpeg` body.

6. **Browser plays response.** As MP3 bytes arrive, `<audio>` plays via `MediaSource` (or simply `URL.createObjectURL(blob)` for v1 simplicity). Mic VAD is **paused during playback** to prevent self-trigger from speaker bleed.

7. **Idle.** Audio ends → state returns to `idle`, VAD resumes.

### Failure modes

| Failure | Handling |
|---|---|
| Backend 5xx / network drop | Browser shows toast "Couldn't reach Rocky," plays a short error tone, returns to `idle`. |
| STT returns empty transcript | Skip Gemini, return a generic "Sorry, didn't catch that" pre-recorded clip. |
| Gemini error (rate limit, malformed response) | Backend returns a generic "Let me think about that" response with HTTP 200 instead of failing. |
| ElevenLabs TTS error | Return text response in JSON; browser falls back to Web Speech `speechSynthesis` for one turn. |
| User speaks during playback | (Stretch) Barge-in: fade out audio, transition to `recording`. Drop if behind schedule. |

## Memory system

### Two persistence tiers

**`memories.json` — long-term facts** (persists across sessions, the "adaptive" surface)

```json
[
  {"id": "m1", "fact": "User's dog is named Lily, a corgi.", "saved_at": 1715235600.0},
  {"id": "m2", "fact": "User is a software engineer at a startup in Singapore.", "saved_at": 1715235800.0},
  {"id": "m3", "fact": "User prefers concise answers without preamble.", "saved_at": 1715236000.0}
]
```

**`conversation.json` — short-term context** (rolling window, last 20 turns)

```json
[
  {"user": "what's the weather like?", "assistant": "I don't have web tools yet, friend. Question?", "ts": 1715236000.0}
]
```

### How memory enters the prompt

Before every Gemini call, the system prompt is rebuilt:

```
[Persona block — see Persona section]

[MEMORIES]
You know these things about the user:
- User's dog is named Lily, a corgi.
- User is a software engineer at a startup in Singapore.
- User prefers concise answers without preamble.

[RECENT CONVERSATION]
User: did I tell you about Lily?
Assistant: Yes, your corgi.
[...8 more turns...]

[NEW INPUT]
{transcript}
```

### Compaction

When `memories.json` exceeds **30** entries, a non-blocking background task calls Gemini with "Summarize these 30 memories into 10 dense facts" and replaces the file. Compaction is opportunistic, runs only on `/turn` boundaries, never blocks the response. The conversation log is naturally bounded by trim-to-20 on append.

### The `remember` tool

```python
remember(fact: str)
  - Append {id, fact, saved_at} to memories.json
  - Broadcast {"type": "memory_added", "fact": ...} on /ws
  - Return {"ok": True}
```

Persona instruction (in the system prompt):

> When the user shares something durable about themselves, their preferences, their world, or the people in their life, call `remember` with a one-sentence fact. Do not call it for trivial small talk or for things that are obvious from context. Do not duplicate facts you already remember.

The "no duplicates" rule is enforced by the prompt; we don't dedupe on the server side. If duplicates slip through, compaction will clean them.

## Persona

Rocky as an assistant: **competent, plain-spoken, warmly curious.** Not the broken-English alien from the book — that's annoying for an assistant. Rocky's "accent" shows up as:

- **Short, direct sentences.** No preamble, no "Of course!", no "I'd be happy to."
- **A characteristic word at uncertainty: "Question?"** (book-faithful Rocky tic, used sparingly — 1 in 5 turns at most).
- **Curiosity.** When ambiguous, asks a follow-up rather than guessing.
- **No corporate-AI hedging.** No "as an AI language model," no "I cannot," no excessive caveats.

System prompt skeleton:

```
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

### ElevenLabs voice choice

The `.env` file holds `ELEVENLABS_VOICE_ID`. Two paths:

1. **Stock voice from the ElevenLabs library** — pick a voice that feels alien-but-warm. Recommend trying `Adam` (deep, calm) or `Drew` (warm, neutral) first. 5 minutes of trial-and-listening.
2. **Voice clone Rocky** — Creator tier unlocks Instant Voice Cloning. If you can grab ~3 minutes of an audiobook narrator doing Rocky's voice, the demo gets dramatically better. Stretch goal.

## File layout

```
pi-rocky/
  rocky.py                 # FastAPI bootstrap (~50 lines)
  llm.py                   # Gemini Flash wrapper + remember tool dispatch
  stt.py                   # ElevenLabs Scribe wrapper
  tts.py                   # ElevenLabs TTS wrapper (streaming)
  memory.py                # Renamed from vocab.py — same shape, different field names
  prompts/
    rocky_system.md        # New persona prompt (replaces v1)
  web/
    server.py              # FastAPI app — endpoints /turn, /memories, /ws, /
    static/
      index.html           # The app: video, mic indicator, transcript, memories
      app.js               # Page state + WebSocket client
      recorder.js          # VAD + MediaRecorder + frame snapshot + POST
  tests/
    test_memory.py         # Renamed from test_vocab.py
  memories.json            # Persisted (gitignored)
  conversation.json        # Persisted (gitignored)
  .env                     # GEMINI_API_KEY, ELEVENLABS_API_KEY, ELEVENLABS_VOICE_ID
```

### Files deleted in this pivot

- `audio.py`, `camera.py`, `idle.py`, `brain.py`
- `tests/test_idle.py`
- `scripts/smoke.py`, `scripts/cam_smoke.py`, `scripts/audio_smoke.py`, `scripts/brain_smoke.py`, `scripts/web_local.py`

The Pi-specific scaffolding (`scripts/setup_pi.sh`, `scripts/sync.sh`, `Makefile` sync targets) stays untouched — it's still useful when you eventually deploy the FastAPI backend to the Pi.

## Sponsor track strategy

- **Primary: ElevenLabs.** Scribe (STT) + TTS + (stretch) custom voice clone. The single most differentiated thing in the demo is what Rocky sounds like. Strong shot.
- **Secondary: Gemini.** Brain is `gemini-2.5-flash` with a tool call. Tracks "media/voice" loosely.
- **Stretch: Adaptation Labs.** Their pitch is adaptive AI / memory systems. The `memories.json` + compaction + how Rocky's responses change as memories accumulate is a natural fit. A 1-page write-up of "how Rocky adapts" would round out the submission.
- **Optional: Convex.** Drop-in replacement for `memories.json` + WebSocket. ~45 min effort. Skip if behind.

## Demo arc (~60 seconds)

1. Walk up. Page already open, video preview live, mic indicator pulsing in idle.
2. *"Hi Rocky, I'm Ariel."* → Rocky: "Hello, Ariel. Question?"
3. *"My dog is a corgi named Lily."* → Memory chip animates in: *"User's dog is named Lily, a corgi."* Rocky: "Lily. Got it."
4. *Hold up a coffee mug.* "What do you see?" → Rocky: "A mug. Coffee?"
5. *Refresh the page* (proves persistence). "What do you remember about my dog?" → Rocky: "Lily. Corgi."
6. *Adaptive moment.* "I prefer no preamble." → memory adds → next reply is noticeably terser, demonstrating that the assistant *adapted*.

## Timeline

| Hour | Goal | Done when |
|---|---|---|
| 0–0.5 | Delete obsolete code, rename `vocab.py` → `memory.py`, all renamed tests pass | `pytest -v` green, no references to `audio.py`/`camera.py`/`brain.py`/`idle.py` remain |
| 0.5–1 | Three thin wrappers: `stt.py`, `tts.py`, `llm.py`. Smoke each with curl/python REPL. | Each module has a `if __name__ == "__main__"` example that runs end-to-end |
| 1–2 | Backend `/turn` endpoint wires STT → LLM → TTS. Test with `curl -F audio=@sample.wav -F image=@sample.jpg`. | Curl returns MP3 bytes, transcript appears in logs, memory tool calls land in `memories.json` |
| 2–3 | Frontend: `getUserMedia`, VAD, MediaRecorder, frame snapshot, POST, audio playback | Speak in browser → hear Rocky reply. Round-trip works. |
| 3–3.5 | Frontend polish: live memory log, transcript bubble, status pill | Demo arc step 3 (memory chip animation) works end-to-end |
| 3.5–4 | Persona tuning + 3× demo dry-runs | Demo arc end-to-end without stumbles |
| 4–5+ | Stretch: voice clone, Adaptation Labs writeup, deploy to Pi | Whatever survives the schedule |

Total: ~4 hours of build, ~5 hours including stretch. Plenty of headroom in 7.

## Out of scope

- Server-side audio capture (deliberately gone).
- Server-side video capture (deliberately gone).
- Wake-word detection (VAD on always-on mic is enough for a demo).
- Multi-user / authentication (single-user single-session).
- Embeddings / vector retrieval — flat memory list with summarization is enough for the demo and avoids a vector DB.
- TTS streaming via Server-Sent Events (we stream MP3 bytes via plain HTTP response body — simpler).
- Barge-in during playback (stretch only).
- Mobile-specific layouts (we'll work on desktop; phone is a stretch).
