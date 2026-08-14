# dockslot-ai
AI agent for handling driver delays, finding feasible dock appointments, and coordinating competing requests.

Based on the SetuHaul FDE Challenge brief (see `docs/developer/architecture.md` for the problem summary and design).

## Getting started

Requires Python 3.9.6 (see `docs/developer/development.md` if you have multiple Pythons installed).

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1   # .venv/bin/activate on macOS/Linux
pip install -r requirements.txt

python scripts\build_db.py   # materializes data/dockslot.db from db/schema_and_seed.sql
python scripts\demo.py       # walks through the deterministic layer against real seeded cases
```

## Docs

- [docs/developer/architecture.md](docs/developer/architecture.md) — system design, layers, key decisions
- [docs/developer/development.md](docs/developer/development.md) — local setup, running, rebuilding the DB
- [docs/ai-agent/context.md](docs/ai-agent/context.md) — project state and conventions for AI coding agents
