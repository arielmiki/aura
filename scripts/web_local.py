"""Mac-only local test of the web UI.

Stands up the FastAPI server with a real VocabStore and a fake camera that
returns a placeholder PNG. Lets you open http://localhost:8000 to verify the
page loads, the WebSocket connects, and learn_word events propagate.
"""
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import base64  # noqa: E402

from vocab import VocabStore  # noqa: E402
from web.server import make_app, serve  # noqa: E402


# 1x1 transparent PNG (placeholder when no real camera).
PLACEHOLDER_JPEG = base64.b64decode(
    "/9j/4AAQSkZJRgABAQEASABIAAD/2wBDAAEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/2wBDAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEB"
    "AQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQEBAQH/wAARCAABAAEDASIAAhEBAxEB/8QA"
    "FQABAQAAAAAAAAAAAAAAAAAAAAr/xAAUEAEAAAAAAAAAAAAAAAAAAAAA/8QAFAEBAAAAAAAAAAAAAAAA"
    "AAAAAP/EABQRAQAAAAAAAAAAAAAAAAAAAAD/2gAMAwEAAhEDEQA/AL+AB//Z"
)


class FakeCamera:
    def latest_jpeg(self) -> bytes:
        return PLACEHOLDER_JPEG


async def main() -> None:
    vocab = VocabStore(
        path=Path("/tmp/rocky_local_vocab.json"),
        seed=["YES", "NO", "HUMAN", "ROCKY", "QUESTION", "NEW", "WORD", "HELLO"],
    )
    cam = FakeCamera()
    status_value = "idle"

    app = make_app(vocab, cam, lambda: status_value)
    print("web UI: http://localhost:8000/")
    print("from another shell, run:")
    print("  python -c \"import json; "
          "from pathlib import Path; "
          "p = Path('/tmp/rocky_local_vocab.json'); "
          "data = json.loads(p.read_text()); "
          "data.append({'word': 'PEN', 'description': 'long thin', 'learned_at': 0}); "
          "p.write_text(json.dumps(data))\"")
    print("(but the WebSocket only updates when learn_word() is called from this process)")
    print()
    print("Easier: from another python REPL or script, call:")
    print("  from vocab import VocabStore")
    print("  v = VocabStore(path='/tmp/rocky_local_vocab.json', seed=[])")
    print("  v.learn_word('PEN', 'long thin object')")
    print("(but you'd have a separate VocabStore instance, so events won't reach the server)")
    print()
    print("Most practical test: just verify the seeded vocab renders. Press Ctrl-C to exit.")

    # Schedule a self-test that learns a word after 5 seconds, so the user
    # can see live WebSocket update behavior in their browser.
    async def self_test():
        await asyncio.sleep(5)
        vocab.learn_word("PEN", "long thin object — auto-added by self-test")
        await asyncio.sleep(3)
        vocab.learn_word("MUG", "ceramic cup — auto-added by self-test")

    asyncio.create_task(self_test())
    await serve(app, port=8000)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
