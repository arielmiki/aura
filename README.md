# Aura

> Every presence has a personality.

Aura is a browser-based AI companion that **sees what you see, hears how you say it, and remembers the things that matter**. Pick a character, talk naturally, and they adapt to you over time.

Built at **AI Engineer Singapore Hackathon 2026**.

![status](https://img.shields.io/badge/status-hackathon-7be8a4)
![python](https://img.shields.io/badge/python-3.9%2B-a89bff)
![license](https://img.shields.io/badge/license-MIT-62d4ff)

---

## What it is

A FastAPI app + a browser front-end. Open the page, allow camera and microphone, and talk. The companion listens, looks, and replies in a cloned voice — while quietly building a personalized memory and tuning its style to you.

**Two characters out of the box:**

- **Rocky** — fragmented-English alien in a cloned ElevenLabs voice ("Amaze! Question?"). Inspired by *Project Hail Mary*.
- **Hana** — bright, energetic anime companion in plain English with a cute voice preset.

Switch any time from the header. Memory and conversation history follow you between characters.

---

## Features

- **Audio-native multimodal turn**: a single Gemini call takes the user's audio + camera frame + memory and returns both the transcription and the spoken reply. No separate STT step in the hot path.
- **Visual memory**: every saved fact captures a snapshot of the camera frame at that moment. Ask about it later — it recalls what it saw.
- **Three-layer adaptive memory**:
  1. Heuristic patterns (reply length, recurring topics) update in real time
  2. Semantic memory facts (text + visual caption) saved via tool call
  3. Adaption Labs corpus auto-cycles every 30 turns to refine long-term style
- **Always-on or push-to-talk** mic, with a sensitivity slider for noisy rooms.
- **Streaming TTS** via ElevenLabs Flash for sub-second first-audio latency.
- **Pluggable LLM backend** — Gemini by default, OpenAI as a one-env-var swap.

---

## Tech stack

| Layer | Provider | Role |
|---|---|---|
| Brain | Google **Gemini 3.1 Flash Lite Preview** (multimodal) | Hears, sees, remembers, replies — all in one call |
| Voice (input) | **ElevenLabs Scribe** | STT fallback when audio-native is disabled |
| Voice (output) | **ElevenLabs** v3 / Flash TTS, with a custom **voice clone** for Rocky | Streaming MP3 in the character's voice |
| Adaptation | **Adaption Labs** | Continuous corpus refinement of style, topics, knowledge |
| Backend | **FastAPI + Uvicorn** | One process, one `/turn` endpoint |
| Front-end | Vanilla JS + Canvas + Web Audio API | No build step |

---

## Quick start

### 1. Clone and create a virtualenv

```bash
git clone https://github.com/arielmiki/aura.git
cd aura
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. Add your API keys to `.env`

```env
GEMINI_API_KEY=...
ELEVENLABS_API_KEY=...
ELEVENLABS_VOICE_ID=...     # any default voice; characters override per voice
ADAPTION_API_KEY=...        # optional — disables Adaption Labs if missing

# Optional — only set if you want to swap the brain backend
# OPENAI_API_KEY=...
# BRAIN_BACKEND=openai
# OPENAI_MODEL=gpt-5
```

### 3. Run it

```bash
uvicorn rocky:app --port 8000
```

Open [http://localhost:8000](http://localhost:8000) for the landing page, or jump directly into the app at [http://localhost:8000/app](http://localhost:8000/app).

---

## Architecture

```
Browser (web/static/)
  ├─ getUserMedia (mic + camera, always on)
  ├─ MediaRecorder (audio chunks)
  ├─ Canvas FFT visualizer (sphere)
  └─ Two slide-in drawers (CONVO / MEMORY)
                │
                ▼  POST /turn (audio + jpeg)
                │
FastAPI (web/server.py)
  ├─ /turn        → respond_audio() → Gemini multimodal (audio + image + memory)
  │                 ↓ on fail, falls back to: Scribe STT → respond() (text)
  ├─ /api/memories, /api/conversation, /api/patterns, /api/adapt
  └─ /ws (push: memory_added, pattern_updated, adapt_status)
                │
                ▼
Persistence
  ├─ memories.json    — { fact, visual_caption, image }
  ├─ conversation.json — rolling window of turns
  └─ patterns.json     — adaptive style (reply length, topic counts, etc.)
```

### Key modules

| File | Responsibility |
|---|---|
| `rocky.py` | App entry point — wires everything, picks brain backend |
| `llm.py` | `Brain` — Gemini multimodal with tool calls (`remember`, `recall_visual`) |
| `llm_openai.py` | `BrainOpenAI` — alternate backend, same interface |
| `stt.py` | ElevenLabs Scribe (fallback path only) |
| `tts.py` | ElevenLabs streaming TTS, per-character voice |
| `memory.py` | Persistent fact store with visual captions |
| `conversation.py` | Rolling conversation log |
| `patterns.py` | Heuristic adaptive style (reply length, topics) |
| `adapt.py` | Adaption Labs corpus integration |
| `characters.py` | Character registry (Rocky, Hana) |
| `prompts/` | Per-character persona templates |
| `web/server.py` | FastAPI app, all HTTP/WS endpoints |
| `web/static/` | `landing.html`, `index.html` (app), `recorder.js`, `app.js`, `favicon.svg` |

---

## Configuration

Environment variables (all optional unless noted):

| Variable | Default | Purpose |
|---|---|---|
| `GEMINI_API_KEY` | **required** | Google Gemini API |
| `ELEVENLABS_API_KEY` | **required** | ElevenLabs (STT + TTS) |
| `ELEVENLABS_VOICE_ID` | **required** | Default voice (overridden per character) |
| `ADAPTION_API_KEY` | — | Enables Adaption Labs cycle |
| `BRAIN_BACKEND` | `gemini` | Set to `openai` to swap LLM provider |
| `BRAIN_MODEL` | `gemini-2.5-flash` | Text-path model (legacy STT pipeline) |
| `BRAIN_AUDIO_MODEL` | `gemini-3.1-flash-lite-preview` | Audio-native multimodal model |
| `BRAIN_AUDIO_NATIVE` | `1` | Set to `0` to force the legacy STT path |
| `OPENAI_API_KEY` | — | Required if `BRAIN_BACKEND=openai` |
| `OPENAI_MODEL` | `gpt-5.5` | Model ID for the OpenAI backend |
| `ROCKY_STT_LANG` | `en` | Force Scribe STT language (ISO-639-1) |

---

## Hackathon notes

This repo started as a Raspberry Pi voice assistant for kids and pivoted twice during the hackathon — first to a browser-based companion with Gemini Live, then to the current architecture (multimodal Gemini + ElevenLabs + Adaption Labs). The git history tells the whole story.

The earlier Pi-specific code (audio device routing, GPIO, sounddevice plumbing) was deleted in favor of `getUserMedia` in the browser, which solved cross-platform support and let us focus on the experience.

---

## License

MIT
