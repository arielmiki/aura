"""FastAPI app for Rocky personal assistant.

Endpoints:
  GET  /              -> static page (web/static/index.html)
  POST /turn          -> multipart audio + image -> mp3 stream
  GET  /api/memories  -> current memories
  WS   /ws            -> push memory_added / memory_compacted events
"""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, File, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response, StreamingResponse
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
             patterns,
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

    @app.get("/api/patterns")
    async def api_patterns():
        return patterns.state()

    @app.get("/memory/image/{entry_id}")
    async def memory_image(entry_id: str):
        # Strict id format guard — entry ids are 8-char uuid hex prefixes.
        if not entry_id.isalnum() or len(entry_id) > 32:
            return Response(status_code=404)
        p = memory.image_path(entry_id)
        if not p:
            return Response(status_code=404)
        return FileResponse(p, media_type="image/jpeg")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        memq = await memory.subscribe()
        patq = await patterns.subscribe()
        await websocket.send_text(json.dumps({
            "type": "snapshot",
            "entries": memory.entries(),
            "patterns": patterns.state(),
        }))

        async def fan_in():
            # Race the two queues; whichever has an event first wins.
            mem_task = asyncio.create_task(memq.get())
            pat_task = asyncio.create_task(patq.get())
            try:
                while True:
                    done, _ = await asyncio.wait(
                        {mem_task, pat_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in done:
                        ev = t.result()
                        await websocket.send_text(json.dumps(ev))
                        if t is mem_task:
                            mem_task = asyncio.create_task(memq.get())
                        else:
                            pat_task = asyncio.create_task(patq.get())
            finally:
                for t in (mem_task, pat_task):
                    t.cancel()

        try:
            await fan_in()
        except WebSocketDisconnect:
            pass
        finally:
            memory.unsubscribe(memq)
            patterns.unsubscribe(patq)

    @app.post("/turn")
    async def turn(audio: UploadFile = File(...),
                   image: UploadFile = File(None)):
        audio_bytes = await audio.read()
        image_bytes = await image.read() if image else None
        log.info("turn: audio=%d bytes image=%d bytes",
                 len(audio_bytes), len(image_bytes) if image_bytes else 0)

        # 1. STT
        try:
            transcript = await asyncio.to_thread(stt.transcribe, audio_bytes)
        except Exception:
            log.exception("STT failed")
            return _fallback_response(tts, "Sorry. Trouble hearing you. Question?")

        if not transcript:
            return _fallback_response(tts, "Sorry. Didn't catch that.")

        log.info("user: %s", transcript)

        # 1.5. Adaptive pattern detection — let signals like "shorter please"
        # update Rocky's preferred reply length BEFORE the brain runs, so the
        # very next reply already reflects the new preference.
        try:
            patterns.apply_user_signal(transcript)
        except Exception:
            log.exception("pattern detection failed")

        # 2. Brain
        try:
            reply = await brain.respond(
                transcript=transcript,
                image_jpeg=image_bytes,
                memories=memory.facts(),
                conversation=conversation.recent(10),
                patterns=patterns.render_for_prompt(),
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
