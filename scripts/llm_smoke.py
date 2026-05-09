"""Smoke test: send a stub turn through the brain, print the reply.

Verifies: (a) Gemini auth works, (b) the prompt template loads, (c) tool
calls dispatch to our handler, (d) response text comes back."""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from dotenv import load_dotenv  # noqa: E402

from llm import Brain  # noqa: E402


async def fake_remember(fact: str) -> str:
    print(f"  -> remember({fact!r})")
    return "fakeid"


async def main() -> None:
    load_dotenv()
    template = Path("prompts/rocky_system.md").read_text()
    brain = Brain(template, on_remember=fake_remember)

    transcript = "Hi Rocky. My dog is a corgi named Lily."
    print(f"user: {transcript!r}")
    reply = await brain.respond(
        transcript=transcript,
        image_jpeg=None,
        memories=[],
        conversation=[],
    )
    print(f"rocky: {reply!r}")


if __name__ == "__main__":
    asyncio.run(main())
