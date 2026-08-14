# dockslot-ai
AI agent for handling driver delays, finding feasible dock appointments, and coordinating competing requests.

Based on the SetuHaul FDE Challenge brief (see `docs/developer/architecture.md` for the problem summary and design).

## Getting started

Requires Python 3.9.6 (see `docs/developer/development.md` if you have multiple Pythons installed).

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1   # .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
copy .env.example .env       # then fill in an API key -- see docs/developer/development.md

python scripts\build_db.py   # materializes data/dockslot.db from db/schema_and_seed.sql
python scripts\demo.py       # deterministic layer against real seeded cases (no API key needed)
python scripts\chat_demo.py  # conversational layer walkthrough -- calls a real LLM
python scripts\chat.py       # interactive chat -- talk to it yourself, calls a real LLM
python scripts\scheduling_demo.py  # optional facility-wide scheduling engine (no API key needed)
python scripts\serve.py      # REST API + UI -- open http://127.0.0.1:8000
```

## Docs

- [docs/developer/architecture.md](docs/developer/architecture.md) — system design, layers, key decisions
- [docs/developer/development.md](docs/developer/development.md) — local setup, running, rebuilding the DB, tests
- [docs/developer/roadmap.md](docs/developer/roadmap.md) — what's done, what's next
- [docs/ai-agent/context.md](docs/ai-agent/context.md) — project state and conventions for AI coding agents
