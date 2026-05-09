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

async def _on_remember(fact: str, image_jpeg=None):
    return memory.remember(fact, image_jpeg=image_jpeg)

brain = Brain(prompt, on_remember=_on_remember)

app = make_app(memory, conversation, brain, stt, tts)
