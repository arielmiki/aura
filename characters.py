"""Character registry. Each entry is a complete companion configuration:
identity, voice, persona prompt, and avatar styling key."""
from __future__ import annotations

from pathlib import Path
from typing import Optional


class Character:
    def __init__(self, id: str, name: str, voice_id: str,
                 prompt_path: Path, description: str = "") -> None:
        self.id = id
        self.name = name
        self.voice_id = voice_id
        self.prompt_path = Path(prompt_path)
        self.description = description

    def prompt(self) -> str:
        return self.prompt_path.read_text()

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "description": self.description}


# Voice IDs:
#   Rocky cloned voice from earlier session
#   Hana — using a stock anime-fitting voice from the ElevenLabs library
#   ("Sarah" — bright, expressive female: EXAVITQu4vr4xnSDxMaL is a safe placeholder)
CHARACTERS = {
    "rocky": Character(
        id="rocky",
        name="Rocky",
        voice_id="CnqVF2NP4RyKei6Tpqwp",  # the cloned Rocky voice
        prompt_path=Path("prompts/rocky.md"),
        description="Alien from Project Hail Mary. Friendly, fragmented English.",
    ),
    "hana": Character(
        id="hana",
        name="Hana",
        voice_id="EXAVITQu4vr4xnSDxMaL",  # Sarah — bright female; swap to a custom anime clone later
        prompt_path=Path("prompts/hana.md"),
        description="Cheerful anime-style companion. Warm, playful, expressive.",
    ),
}

DEFAULT_CHARACTER = "rocky"
