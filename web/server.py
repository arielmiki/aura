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
from tts import TTS, detect_language

log = logging.getLogger(__name__)

STATIC = Path(__file__).parent / "static"
AUTO_ADAPT_EVERY_N_TURNS = 30  # check categories every N turns; hash gate skips unchanged


def make_app(memory: MemoryStore,
             conversation: ConversationLog,
             patterns,
             adapter,
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

    @app.get("/api/adapt")
    async def api_adapt_get():
        return {"state": adapter.state(), "configured": adapter.configured,
                "turn_count": len(conversation.turns())}

    @app.post("/api/adapt")
    async def api_adapt_post():
        if not adapter.configured:
            return {"error": "ADAPTION_API_KEY not set"}
        try:
            state = await adapter.adapt(
                turns=conversation.turns(),
                memory_entries=memory.entries(),
                patterns_state=patterns.state(),
            )
            return {"state": state}
        except Exception as e:
            log.exception("adapt failed")
            return {"error": f"{type(e).__name__}: {e}"}

    @app.post("/api/adapt/refresh")
    async def api_adapt_refresh():
        if not adapter.configured:
            return {"error": "ADAPTION_API_KEY not set"}
        return {"state": await adapter.refresh()}

    @app.get("/memory/image/{entry_id}")
    async def memory_image(entry_id: str):
        # Strict id format guard — entry ids are 8-char uuid hex prefixes.
        if not entry_id.isalnum() or len(entry_id) > 32:
            return Response(status_code=404)
        p = memory.image_path(entry_id)
        if not p:
            return Response(status_code=404)
        return FileResponse(p, media_type="image/jpeg")

    @app.delete("/api/memories/{entry_id}")
    async def api_memory_delete(entry_id: str):
        if not entry_id.isalnum() or len(entry_id) > 32:
            return {"ok": False, "error": "invalid id"}
        return {"ok": memory.forget(entry_id)}

    @app.delete("/api/memories")
    async def api_memories_clear():
        return {"ok": True, "removed": memory.clear()}

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        memq = await memory.subscribe()
        patq = await patterns.subscribe()
        adaq = await adapter.subscribe()
        await websocket.send_text(json.dumps({
            "type": "snapshot",
            "entries": memory.entries(),
            "patterns": patterns.state(),
            "adapt": adapter.state(),
            "turn_count": len(conversation.turns()),
            "adapt_configured": adapter.configured,
        }))

        async def fan_in():
            tasks = {
                "mem": asyncio.create_task(memq.get()),
                "pat": asyncio.create_task(patq.get()),
                "ada": asyncio.create_task(adaq.get()),
            }
            try:
                while True:
                    done, _ = await asyncio.wait(
                        set(tasks.values()),
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    for t in done:
                        ev = t.result()
                        await websocket.send_text(json.dumps(ev))
                        # respawn the corresponding queue waiter
                        for k, v in list(tasks.items()):
                            if v is t:
                                src = {"mem": memq, "pat": patq, "ada": adaq}[k]
                                tasks[k] = asyncio.create_task(src.get())
            finally:
                for t in tasks.values():
                    t.cancel()

        try:
            await fan_in()
        except WebSocketDisconnect:
            pass
        finally:
            memory.unsubscribe(memq)
            patterns.unsubscribe(patq)
            adapter.unsubscribe(adaq)

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

        # 2. Brain — compute a small inline recap of memories most relevant
        # to this transcript. These get injected with the user's message so
        # Gemini cannot ignore them the way it sometimes does with long
        # system prompts.
        recall_hits = memory.relevant(transcript, top_k=3)
        inline_recall = [e["fact"] for e in recall_hits] if recall_hits else None

        try:
            reply = await brain.respond(
                transcript=transcript,
                image_jpeg=image_bytes,
                memories=memory.facts(),
                conversation=conversation.recent(5),  # was 10; faster prompt
                patterns=patterns.render_for_prompt(),
                adapted_knowledge=adapter.render_for_prompt(),
                inline_recall=inline_recall,
            )
        except Exception:
            log.exception("Brain failed")
            return _fallback_response(tts, "Let me think about that.")

        log.info("rocky: %s", reply)

        # 3. Append to conversation log
        conversation.append(transcript, reply)

        # 3.5. Auto-adaptation: every N turns, kick off an adapt cycle. The
        # adapter's hash gate per category will skip any category that hasn't
        # changed since the last upload, so this is cheap.
        turn_count = len(conversation.turns())
        if (adapter.configured
                and turn_count > 0
                and turn_count % AUTO_ADAPT_EVERY_N_TURNS == 0
                and adapter.state().get("status") not in ("uploading", "running")):
            log.info("auto-adapt: triggering at turn %d", turn_count)
            asyncio.create_task(_run_adapt(
                adapter, conversation.turns(), memory.entries(), patterns.state()
            ))

        # 4. TTS — Rocky's output is always English by policy (the persona
        # prompt enforces this). Pin language_code='en' so the voice stays
        # consistent even if Gemini accidentally drops in a foreign word.
        return StreamingResponse(
            _stream_tts(tts, reply, "en"),
            media_type="audio/mpeg",
            headers={"X-Transcript": _safe_header(transcript),
                     "X-Reply": _safe_header(reply),
                     "X-Lang": "en"},
        )

    return app


def _safe_header(s: str) -> str:
    """Sanitize for ASCII-only HTTP header value (avoid latin-1 errors)."""
    return s.encode("ascii", "ignore").decode("ascii")[:500]


async def _stream_tts(tts: TTS, text: str, language_code: str = None) -> AsyncIterator[bytes]:
    async for chunk in tts.stream(text, language_code=language_code):
        yield chunk


async def _run_adapt(adapter, turns, memory_entries, patterns_state) -> None:
    """Background-task wrapper around adapter.adapt() that swallows errors
    so a transient Adaption Labs hiccup never breaks the chat path."""
    try:
        await adapter.adapt(
            turns=turns,
            memory_entries=memory_entries,
            patterns_state=patterns_state,
        )
    except Exception:
        log.exception("auto-adapt failed")


def _fallback_response(tts: TTS, text: str) -> StreamingResponse:
    return StreamingResponse(
        _stream_tts(tts, text),
        media_type="audio/mpeg",
        headers={"X-Reply": _safe_header(text), "X-Fallback": "1"},
    )
