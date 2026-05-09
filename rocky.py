"""ASGI entry point. Run with:  uvicorn rocky:app --reload --port 8000"""
from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

from adapt import Adapter
from conversation import ConversationLog
from llm import Brain, caption_image
from memory import MemoryStore
from patterns import PatternStore
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
patterns = PatternStore(path=Path("patterns.json"))
adapter = Adapter(blueprint_path=Path("prompts/rocky_system.md"))
stt = STT()
tts = TTS()
prompt = Path("prompts/rocky_system.md").read_text()

import os
from google import genai
gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


async def _on_remember(fact: str, image_jpeg=None):
    """Save a fact, enriching it with a one-sentence visual description
    of the moment it was learned. The image's pixels are stored on disk
    AND its content gets baked into the fact text so memory recall works
    even when the actual image isn't sent back to Gemini."""
    enriched = fact
    if image_jpeg:
        try:
            visual = await caption_image(gemini_client, image_jpeg)
            if visual:
                enriched = f"{fact.rstrip('.')} — {visual}"
                logging.getLogger('rocky').info("caption: %s", visual)
        except Exception:
            logging.getLogger('rocky').exception("caption failed")
    return memory.remember(enriched, image_jpeg=image_jpeg)


async def _on_recall_visual(query: str):
    """Look up a memory matching `query` so Rocky can recall past visuals."""
    entry = memory.recall(query)
    if not entry:
        return None
    logging.getLogger('rocky').info("recall_visual(%r) -> %s", query, entry["id"])
    return {
        "id": entry["id"],
        "fact": entry["fact"],
        "saved_at": entry.get("saved_at"),
    }


brain = Brain(
    prompt,
    on_remember=_on_remember,
    on_recall_visual=_on_recall_visual,
)

app = make_app(memory, conversation, patterns, adapter, brain, stt, tts)
