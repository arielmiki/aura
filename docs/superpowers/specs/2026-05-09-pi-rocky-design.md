# Pi-Rocky — Design

**Date:** 2026-05-09
**Author:** ratrekt
**Event:** AI Engineer Singapore Hackathon (7 hours, solo)

## What we're building

A Raspberry Pi sitting on a desk that behaves like Rocky from *Project Hail Mary*: a curious alien who doesn't speak English, observes the world through a camera, and learns words from a human in real time. The human holds up an object, names it, and Rocky adds the word to its vocabulary — visibly, on a live web page — and uses it from that point on. When nobody is teaching, Rocky idly comments on what it sees in fragmented "Rocky-speak."

The goal is a 60-second demo a judge can interact with: walk up, teach Rocky a word, watch Rocky use it. The pitch lines up squarely with the **Gemini track (media/voice)**.

## Hardware

- **Raspberry Pi 3B** running Pi OS Bookworm
- **CSI ribbon Pi camera** (existing)
- **Blue Snowball USB microphone** (existing)
- **Wired speaker via 3.5mm jack** (existing); Ugreen 3.5mm-to-Bluetooth transmitter as backup wireless option
- **Tailscale** already installed — provides reliable network and reachable web UI from phone/laptop
- HyperX 7.1 USB headset is a dev tool only (avoid feedback while iterating); not in the demo

Pi 3B has no AI compute; it is purely an I/O device. All cognition lives in cloud APIs.

## System overview

```
┌────────────────────────────────────────────────────────────┐
│  Raspberry Pi 3B  (the body)                               │
│                                                            │
│   USB mic  ─┐                                              │
│   CSI cam  ─┼──► rocky.py (single Python asyncio process)  │
│   3.5mm   ◄─┘     │                                        │
│                   ├──► cloud APIs (the brain)              │
│                   └──► localhost:8000 web UI (the display) │
└────────────────────────────────────────────────────────────┘
                           │                  │
                           ▼                  ▼
                     Gemini Live API    Phone/laptop on tailnet
                     (multimodal:        showing live vocab page
                      voice+image
                      in, voice out)
```

A single Python process on the Pi runs the entire system: it streams mic audio to Gemini Live, captures camera frames, plays Gemini's response audio, manages vocabulary state, and serves a local web page that any device on the tailnet can view.

## The brain: Gemini Live

Gemini Live is the **only API in the critical path**. Reasons:

- It is the one service that does multimodal *streaming* — voice in, image in, voice out — in a single session.
- It handles its own voice-activity detection, so we do not need a wake word, push-to-talk button, or VAD library.
- It supports tool calls, which is how we hook word-learning into the conversation.

A long-lived WebSocket session opens at boot and stays alive. Mic audio streams continuously; Gemini decides when the user has finished a turn. Camera frames are captured at ~1 fps into a `latest_frame` variable; when a turn begins, the latest frame is attached as image input for that turn. So Rocky always "sees" what is currently in front of the camera while the human speaks.

### The system prompt

Rocky's personality is shaped entirely by the system prompt. Approximate shape:

> You are Rocky, a curious alien from the book *Project Hail Mary*. You are meeting a human for the first time. You only know these words: `{vocab}`. Speak in short, fragmented phrases — 1 to 4 words. End uncertain statements with "QUESTION?". Never use words outside your vocabulary list. When the human shows you something and tells you its name, call the `learn_word` tool with the new word and a one-sentence description of what they showed you.

The vocab list is rebuilt into the prompt before every new turn — cheap string formatting, no caching to manage.

### Word-learning via tool call

We define one tool:

```
learn_word(word: str, description: str)
  → appends to vocab.json
  → broadcasts WebSocket update to the web UI
```

When the user holds up a pen and says "this is a pen," Gemini sees the image plus audio, decides this is a teaching moment, and calls `learn_word("PEN", "long thin object the human held up")`. Our handler updates `vocab.json` and pushes the new word to the web page. Rocky verbally confirms ("PEN. NEW WORD.") in the same turn.

### Recall

When the user later asks "what is this?" while holding the pen, Gemini sees the image and has the vocab list (with descriptions) in its prompt. It matches "long thin object" to PEN and answers "PEN!" No vector DB, no embeddings, no separate matching layer — Gemini does the matching with its own vision plus the text descriptions we stored.

### Cooperation risk

The biggest risk is Gemini ignoring the "only use these words" constraint and reverting to fluent English. Mitigations, in order:

1. Seed `vocab.json` with `["YES","NO","HUMAN","ROCKY","QUESTION","NEW","WORD","HELLO"]` so Rocky has language from second one and is not silent or babbling.
2. Test the system prompt early in hour 1–2 and iterate.
3. If Gemini cheats, add a post-processing filter that masks unknown words as `[chirp]` before the audio plays.

## Idle behavior

To avoid Rocky feeling dead between teaching moments, an idle loop monitors two signals:

- **Silence:** no user audio in the last 30s
- **Motion:** the latest camera frame differs from the one 5s ago beyond a pixel-diff threshold

When both are true and we haven't fired in the last 60s, we inject a synthetic instruction into the same Gemini Live session: *"(The human is quiet but you can see them. Make a brief observation in 1–3 words using only your vocabulary.)"* Rocky speaks. The cooldown prevents loops. If a real conversation starts mid-comment, Gemini's barge-in handles the interruption.

Same session, same voice, no second pipeline.

## Web UI

A single page at `:8000`, viewable on any tailnet device. Three components:

1. **Vocabulary list** — live-updating, newest word highlighted, with the camera thumbnail captured at the moment the word was learned.
2. **Latest camera frame** — small preview so judges can see what Rocky sees.
3. **Status pill** — `listening` / `thinking` / `speaking` / `idle`.

Stack: FastAPI + Jinja + a single WebSocket pushing JSON deltas to vanilla JS. No framework. ~150 lines of HTML/JS total.

## File layout

```
pi-rocky/
  rocky.py            # entry point, asyncio orchestration
  audio.py            # mic capture (sounddevice), speaker playback
  camera.py           # picamera2 frame grabber
  brain.py            # gemini live session, tool-call dispatcher
  vocab.py            # vocab store + learn_word handler
  idle.py             # silence + motion → synthetic prompt injector
  web/
    server.py         # fastapi + websocket
    static/index.html
    static/app.js
  prompts/rocky_system.md  # the system prompt — edit freely
  vocab.json          # persisted state, starts with seed words
  .env                # GEMINI_API_KEY
```

## Pre-hackathon checklist (do before the clock starts)

Solo + 7 hours is tight. Anything done at home is time saved during the build.

- [ ] Pi 3B re-imaged with Pi OS Bookworm
- [ ] Tailscale running; SSH from laptop to Pi works over tailnet
- [ ] Snowball plugged in; `arecord -l` lists it; `arecord -d 5 t.wav && aplay t.wav` round-trips audio
- [ ] CSI camera ribbon installed; `libcamera-still -o test.jpg` saves a frame
- [ ] Speaker plugged into 3.5mm; `aplay /usr/share/sounds/alsa/Front_Center.wav` plays
- [ ] Gemini API key acquired, billing enabled
- [ ] Hello-world Python script that opens a Gemini Live session and round-trips one voice turn runs end-to-end on the laptop
- [ ] Repo skeleton matching the file layout above committed and pulled to the Pi

If any item isn't checked, do it before the event — much cheaper to debug at home.

## 7-hour build timeline

Claude Code will compress the *coding* portions, but hardware bring-up, personality iteration, and demo rehearsal all consume real wall clock. Treat compressed coding hours as polish/rehearsal buffer, not as room for more features.

| Hour | Goal | Done when |
|------|------|-----------|
| 0–1  | Mic + camera + speaker exercised by tiny scripts on the Pi | `python test_audio.py` records and plays back; `python test_cam.py` saves a frame |
| 1–2  | Gemini Live "Rocky says hello" with hardcoded vocab system prompt | You speak, Rocky replies in fragments, voice plays out the speaker |
| 2–4  | `learn_word` tool call → vocab.json → reflected in next turn's prompt | You teach 3 words live; Rocky uses them in subsequent turns. **This is the demo's heart — protect this slot.** |
| 4–5  | FastAPI server + WebSocket + minimal HTML showing vocab live | Phone on tailnet shows new words appearing in real time |
| 5–6  | Idle behavior (silence + motion → synthetic prompt) | Rocky comments unprompted while you walk past |
| 6–7  | Personality tuning, demo dry-runs, buffer | You can run the demo arc end-to-end 3 times in a row without a stumble |

## Scope cuts (drop in this order if behind)

1. Idle behavior — kill first; the core demo still works without it
2. Camera preview on the web page — vocab list alone is enough
3. Image thumbnails in vocab — text-only list demos fine
4. Pretty CSS — bare HTML reads as "intentionally minimal"

## Demo arc (~60 seconds, rehearse exactly)

1. *(Rocky idle)* "HUMAN. SITTING. QUESTION?"
2. You: "Hello Rocky." → Rocky: "HELLO. HUMAN."
3. *Hold up a pen.* "This is a pen." → Rocky: "PEN. NEW WORD." *(web page lights up)*
4. *Hide pen.* "What was that?" → Rocky: "PEN!"
5. *Hold up a mug.* "Mug." → Rocky: "MUG. NEW WORD."
6. *Hold both.* "Pen or mug?" → Rocky: "PEN. MUG. TWO."
7. *(idle, you step back)* Rocky: "HUMAN. SMILE. QUESTION?"

## Stretch goals (only attempt after the primary demo is solid)

If the core build is locked in by hour 5 and demo rehearsal is going clean, consider in this order:

1. **ElevenLabs voice for Rocky** — replace Gemini's default TTS with a custom alien-feeling voice. ~1 hour swap. Personality multiplier; worth the most of any stretch.
2. **Convex** for vocab persistence — swap `vocab.json` + raw WebSocket for Convex's reactive DB. ~45 minutes. Adds a second sponsor-track shot (Convex is on the listed tracks).
3. **Physical "body"** — even a cardboard shell with a googly eye on the camera. Free, but judges remember projects with character.

## Sponsor track strategy

Aim primarily for the **Gemini track (media/voice)**. This project sits squarely in their sweet spot: multimodal Live API, voice in/out, tool calls, image input. Do not fragment focus across tracks. Convex is a viable secondary shot only if it falls into the schedule for free.

## Out of scope

- On-device speech-to-text or LLM inference (Pi 3B can't)
- Wake-word detection (Gemini Live handles turn-taking)
- Vector embeddings, vector DB, RAG
- Custom voice fine-tuning beyond ElevenLabs presets
- Multi-user (one human at a time)
- Persistent memory across power cycles beyond `vocab.json`
- Anything resembling production deployment, auth, or rate-limit handling
