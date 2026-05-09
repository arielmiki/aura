"""ASGI entry point. Run with:  uvicorn rocky:app --reload --port 8000"""
from __future__ import annotations

import logging
from pathlib import Path

from dotenv import load_dotenv

from adapt import Adapter
from characters import CHARACTERS, DEFAULT_CHARACTER
from conversation import ConversationLog
from llm import Brain, caption_image
# OpenAI backend is loaded lazily below (only if BRAIN_BACKEND=openai)
# so the SDK isn't required when sticking with Gemini.
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

# Active character — shared across the process; switch via the API.
active_character = CHARACTERS[DEFAULT_CHARACTER]
adapter = Adapter(blueprint_path=active_character.prompt_path)
stt = STT()
tts = TTS(voice_id=active_character.voice_id)
prompt = active_character.prompt()

import asyncio
import os
from google import genai
gemini_client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])


async def _on_remember(fact: str, image_jpeg=None):
    """Save the fact IMMEDIATELY so the chat loop isn't blocked. The
    visual caption is computed in the background and merged into the
    entry's fact text when ready (~5-6 s later) — by which time Rocky
    has already finished replying."""
    mid = memory.remember(fact, image_jpeg=image_jpeg)
    if mid and image_jpeg:
        asyncio.create_task(_enrich_with_caption(mid, fact, image_jpeg))
    return mid


async def _enrich_with_caption(mid: str, base_fact: str, image_jpeg: bytes):
    """Background task: caption the image and store as a SEPARATE field.

    We deliberately don't append to the fact — long visual descriptions
    pollute the prompt when memories are injected into every turn. The
    fact stays clean text; the caption is consulted by recall_visual."""
    try:
        visual = await caption_image(gemini_client, image_jpeg)
        if visual:
            memory.update_visual(mid, visual)
            logging.getLogger('rocky').info("caption(bg) %s: %s", mid, visual)
    except Exception:
        logging.getLogger('rocky').exception("caption(bg) failed")


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


# Pick the brain backend. Default: Gemini (audio-native multimodal).
# Set BRAIN_BACKEND=openai to swap to GPT-5.5 (or whatever OPENAI_MODEL
# points to). To revert: unset BRAIN_BACKEND, or set BRAIN_BACKEND=gemini.
_BRAIN_BACKEND = os.environ.get("BRAIN_BACKEND", "gemini").lower()
if _BRAIN_BACKEND == "openai":
    from llm_openai import BrainOpenAI, OPENAI_MODEL
    brain = BrainOpenAI(
        prompt,
        on_remember=_on_remember,
        on_recall_visual=_on_recall_visual,
    )
    logging.getLogger('rocky').info(
        "brain backend = OpenAI (model=%s) — STT path will be used since "
        "OpenAI chat doesn't accept raw audio.", OPENAI_MODEL)
else:
    brain = Brain(
        prompt,
        on_remember=_on_remember,
        on_recall_visual=_on_recall_visual,
    )
    logging.getLogger('rocky').info("brain backend = Gemini")


def switch_character(char_id: str) -> bool:
    """Hot-swap the active character: voice, persona prompt, blueprint."""
    global active_character
    if char_id not in CHARACTERS:
        return False
    active_character = CHARACTERS[char_id]
    tts.set_voice(active_character.voice_id)
    brain.set_template(active_character.prompt())
    adapter.blueprint_path = active_character.prompt_path
    logging.getLogger('rocky').info("switched to character: %s", char_id)
    return True


def current_character_id() -> str:
    return active_character.id

app = make_app(memory, conversation, patterns, adapter, brain, stt, tts,
               switch_character=switch_character,
               current_character_id=current_character_id,
               characters_dict=CHARACTERS)
