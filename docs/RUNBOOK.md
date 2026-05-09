# Pi-Rocky Runbook

Quick reference for bringing Rocky online when you're back at the Pi.

## Pre-flight (one-time, but re-run if anything got moved)

```bash
# From Mac, sync everything to the Pi:
make sync

# On Pi, set up venv (only if missing):
ssh me322@pibot 'cd ~/pi-rocky && bash scripts/setup_pi.sh'

# Add your Gemini API key to ~/pi-rocky/.env on the Pi:
ssh me322@pibot 'cat > ~/pi-rocky/.env <<EOF
GEMINI_API_KEY=YOUR_KEY_HERE
ROCKY_MIC_DEVICE=Snowball
ROCKY_SPEAKER_DEVICE=HyperX
ROCKY_WEB_PORT=8000
EOF'
```

Get a key from https://aistudio.google.com/apikey.

## Smoke test ladder — run in this order, fix what breaks before continuing

Each step verifies one component on its own. If a step fails, don't move on.

### 1. Hardware smoke
```bash
ssh me322@pibot 'cd ~/pi-rocky && . .venv/bin/activate && python scripts/smoke.py'
```
Pass = "smoke OK", peak > 500 (if you spoke), camera frame > 5 KB.

### 2. Camera smoke
```bash
ssh me322@pibot 'cd ~/pi-rocky && . .venv/bin/activate && python scripts/cam_smoke.py'
```
Pass = 5 frames, byte counts > 5000, motion changes when you move.

### 3. Audio I/O smoke
```bash
ssh me322@pibot 'cd ~/pi-rocky && . .venv/bin/activate && python scripts/audio_smoke.py'
```
Pass = "audio smoke OK", you hear your own voice played back.

### 4. Gemini Live smoke (the big one)
```bash
ssh me322@pibot 'cd ~/pi-rocky && . .venv/bin/activate && python scripts/brain_smoke.py'
```
Pass = you speak ("Hello Rocky"), Rocky replies in fragmented English ("HELLO. HUMAN.").

**Most likely failure:** the model name `gemini-2.5-flash-live-preview` may have changed. Check https://ai.google.dev/gemini-api/docs/live for the current Live model name and update `LIVE_MODEL` at the top of `brain.py` if needed.

### 5. Full integration
```bash
make run
```
Same as step 4, but with vocab, camera, and web UI. Open http://pibot:8000/ on your phone (Tailscale).

## The demo arc

Reset vocab between rehearsals: `ssh me322@pibot 'rm -f ~/pi-rocky/vocab.json'`

```
1. Sit silent ~30s while moving slightly.
   → Rocky idles: "HUMAN. ..."

2. "Hello Rocky."                       → "HELLO. HUMAN."

3. Hold up a pen. "This is a pen."     → "PEN. NEW WORD." (web UI lights up)

4. Hide pen. "What was that?"          → "PEN!"

5. Hold up a mug. "Mug."                → "MUG. NEW WORD."

6. Hold both. "Pen or mug?"             → "PEN. MUG. TWO."

7. Step back, stay silent ~30s.
   → Rocky idles again.
```

## Troubleshooting

| Symptom | Likely cause | Fix |
|---|---|---|
| `paInvalidSampleRate` from sounddevice | Audio device hw rate mismatch | Check `.venv/bin/python -c "import sounddevice; print(sounddevice.query_devices())"` and verify "Snowball" / "HyperX" substrings still resolve. |
| Rocky uses fluent English | System prompt being ignored | Try `gemini-2.5-flash` or a stronger model in `brain.py`'s `LIVE_MODEL`. |
| `learn_word` never called | Tool call not firing | Add an explicit example call to `prompts/rocky_system.md`. |
| Web page blank | uvicorn didn't start | Check `make run` output for FastAPI errors; firewall blocking 8000? |
| Camera shows wrong orientation | Pi camera mounted at angle | Set `hflip=True` and/or `vflip=True` in `CameraService(...)` at line 43 of `rocky.py`. |
| 30s idle never fires | VAD threshold too low (always thinks you're speaking) | Bump `> 800` to `> 1500` in `pump_mic` (rocky.py line 83). |

## Stretch goals — only after primary demo is solid

1. **ElevenLabs voice for Rocky** (~1 hr) — replace Gemini's audio modality with text + ElevenLabs TTS for a real alien voice.
2. **Convex** for vocab persistence (~45 min) — second sponsor track shot.
3. **Cardboard body + googly eye** (free) — 10 minutes of crafts; judges remember it.
