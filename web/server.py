"""FastAPI app exposing vocab + camera + status. Runs in-process with rocky.py."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, Response
from fastapi.staticfiles import StaticFiles

import uvicorn

STATIC = Path(__file__).parent / "static"


def make_app(vocab_store, camera_service, status_provider) -> FastAPI:
    """status_provider() -> str  (one of: listening|thinking|speaking|idle)"""
    app = FastAPI()

    @app.get("/")
    async def root():
        return FileResponse(STATIC / "index.html")

    app.mount("/static", StaticFiles(directory=str(STATIC)), name="static")

    @app.get("/api/vocab")
    async def api_vocab():
        return {"entries": vocab_store.entries(), "status": status_provider()}

    @app.get("/frame.jpg")
    async def frame():
        jpg = camera_service.latest_jpeg()
        if not jpg:
            return Response(status_code=204)
        return Response(content=jpg, media_type="image/jpeg")

    @app.websocket("/ws")
    async def ws(websocket: WebSocket):
        await websocket.accept()
        queue = await vocab_store.subscribe()
        # initial snapshot
        await websocket.send_text(json.dumps({
            "type": "snapshot",
            "entries": vocab_store.entries(),
            "status": status_provider(),
        }))
        try:
            while True:
                event = await queue.get()
                await websocket.send_text(json.dumps(event))
        except WebSocketDisconnect:
            pass
        finally:
            vocab_store.unsubscribe(queue)

    return app


async def serve(app: FastAPI, port: int = 8000) -> None:
    config = uvicorn.Config(app, host="0.0.0.0", port=port, log_level="warning")
    server = uvicorn.Server(config)
    await server.serve()
