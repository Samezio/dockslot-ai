# dockslot-ai
AI agent for handling driver delays, finding feasible dock appointments, and coordinating competing requests.

Based on the SetuHaul FDE Challenge brief (see `docs/developer/architecture.md` for the problem summary and design).

## Getting started

**Full step-by-step guide: [docs/SETUP.md](docs/SETUP.md)** — including how to
populate the data, which phone numbers to log in with, and what to do when
something breaks.

The short version (Python 3.9.6, PowerShell):

```powershell
py -3.9 -m venv .venv
.venv\Scripts\Activate.ps1   # .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
copy .env.example .env       # then set LLM_PROVIDER + its API key

python scripts\build_db.py   # populates data/dockslot.db from db/schema_and_seed.sql
python scripts\serve.py      # then open http://127.0.0.1:8000  (log in as 9000010010)
```

Other entry points, all offline except the chat ones:

```powershell
python scripts\demo.py             # deterministic layer against real seeded cases
python scripts\concurrency_demo.py # 5 threads race for 1 slot -> exactly 1 winner
python scripts\scheduling_demo.py  # optional facility-wide scheduling engine
python scripts\chat.py             # interactive chat in the terminal -- calls a real LLM
python scripts\chat_demo.py        # scripted conversational walkthrough -- calls a real LLM
pytest -q                          # 65 deterministic tests, no LLM calls
```

## Docs

- [docs/SETUP.md](docs/SETUP.md) — **start here**: setup, data, running, troubleshooting
- [docs/developer/architecture.md](docs/developer/architecture.md) — system design, layers, key decisions
- [docs/developer/development.md](docs/developer/development.md) — local setup, running, rebuilding the DB, tests
- [docs/developer/roadmap.md](docs/developer/roadmap.md) — what's done, what's next
- [docs/developer/known-issues/](docs/developer/known-issues/README.md) — confirmed bugs and gaps, with reproductions
- [docs/ai-agent/context.md](docs/ai-agent/context.md) — project state and conventions for AI coding agents
