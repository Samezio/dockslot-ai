"""Run the REST API + UI locally.

Run: python scripts\\serve.py
Then open http://127.0.0.1:8000 in a browser.

Needs data/dockslot.db built first (python scripts\\build_db.py) and,
for /chat, .env configured with an LLM provider (see app/llm.py). The
/schedule endpoint is offline/deterministic like scripts/scheduling_demo.py.

For auto-reload during development, run uvicorn directly instead:
  uvicorn app.api:app --reload
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import uvicorn

if __name__ == "__main__":
    uvicorn.run("app.api:app", host="127.0.0.1", port=8000)
