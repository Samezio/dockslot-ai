# Local development

## Python version

The project targets **Python 3.9.6** (see CLAUDE.md section 4). If you have
multiple Pythons installed on Windows, use the `py` launcher to pick it:

```powershell
py -0p                  # lists installed interpreters and their paths
py -3.9 -m venv .venv
```

If `py -3.9` isn't available, install Python 3.9.6 first -- don't build the
venv against a newer interpreter and hope nothing 3.10+-only slips in.

## Setup

```powershell
py -3.9 -m venv .venv
.venv\Scripts\Activate.ps1    # .venv/bin/activate on macOS/Linux
pip install -r requirements.txt
```

`requirements.txt` covers the conversational layer (langchain +
provider integration, pydantic, python-dotenv); the deterministic layer
(`app/db.py`, `app/models.py`, `app/repository.py`) is still stdlib-only.

Note: `google-api-core`/`google-auth` (pulled in by `langchain-google-genai`)
print a `FutureWarning` on import that Python 3.9 is past end-of-life and
won't get further updates from Google. It's a warning, not an error --
everything still runs -- but it's worth knowing this dependency chain is
the reason a 3.9.6 venv might eventually need revisiting.

## Environment variables

```powershell
copy .env.example .env
```

Then fill in the API key for whichever provider `LLM_PROVIDER` in `.env`
points at (default: `google_genai`, needs `GOOGLE_API_KEY`). `.env` is
gitignored -- never commit real keys. See `app/llm.py` for the full
provider list and which env var each one needs.

Both `google_genai` (Gemini) and `openrouter` have been run live and
confirmed working. The `openrouter` entry in `app/llm.py` sets
`max_tokens=300` as a dev-safety cap -- that key has limited credits.
Raise or remove it once this is more than a dev key; don't quietly widen
it while it's still a shared low-credit key.

### `ollama` provider (local dev only)

`LLM_PROVIDER=ollama` runs against a local [Ollama](https://ollama.com)
server instead of a hosted API -- no key, no cost, nothing leaves the
machine. Needs `ollama serve` running and the model pulled:

```powershell
ollama pull qwen3:8b
```

Set `OLLAMA_BASE_URL` in `.env` only if your server isn't at the default
`http://localhost:11434`. This provider is for local iteration -- it's
not something to point production at.

## Database

The tracked source of truth is `db/schema_and_seed.sql`. The actual SQLite
file (`data/dockslot.db`) is gitignored and generated:

```powershell
python scripts\build_db.py
```

Safe to re-run any time -- it deletes and rebuilds `data/dockslot.db` from
scratch, so it always matches whatever's checked into
`db/schema_and_seed.sql`. If you need different/more seed data, edit that
file and rebuild; don't hand-edit the generated `.db`.

Note: the bulk load runs with `PRAGMA foreign_keys` off (the seed script
creates some tables that forward-reference tables defined later in the
file), then runtime connections via `app/db.py::get_connection()` turn
foreign key enforcement back on.

## Running things

```powershell
python scripts\demo.py
```

`scripts/demo.py` inserts the repo root onto `sys.path` itself, so this
works from any shell without setting `PYTHONPATH` -- just `cd` into the
repo root first (relative paths like `data/dockslot.db` in `app/db.py`
are resolved from the script's own location, not from the current
directory, but run scripts from the repo root as a habit anyway).

`scripts/demo.py` is a narrated walkthrough of `app/repository.py` against
real seeded edge cases (ambiguous driver, ETA-vs-appointment feasibility,
a no-feasible-slot escalation, and a two-drivers-race-for-one-slot). Run it
after any change to `app/repository.py` as a quick sanity check. It's fully
offline/deterministic -- no API key needed.

```powershell
python scripts\chat_demo.py
```

`scripts/chat_demo.py` walks the conversational layer (`app/conversation.py`)
through real seeded messages, including a two-turn exchange that actually
books a slot. This one calls a real LLM, so it needs `.env` set up (see
above) and will incur provider API cost/latency each run.

```powershell
python scripts\chat.py [DRIVER_ID]
```

`scripts/chat.py` is the actual interactive way to use this -- an
in-terminal chat loop. It asks for your phone number and looks you up
(no default driver -- mirrors how a real channel like WhatsApp would
identify the sender). `/switch` to re-identify, `/quit` to exit. Seeded
phone numbers are in `drivers.phone` (e.g. `9000010006` = DRV006, Manoj
Sharma); `--driver DRV0xx` skips identification as a dev shortcut and
says so. Same as `chat_demo.py`, this calls a real LLM per message.

```powershell
python scripts\concurrency_demo.py
```

`scripts/concurrency_demo.py` proves the DB's concurrency guard under real
concurrent access (separate threads/connections synchronized to fire
together), not just sequential calls. Offline/deterministic -- no API key
needed. Rebuilds `data/dockslot.db` itself (twice, once per scenario), so
don't run it against a DB state you care about keeping.

```powershell
python scripts\scheduling_demo.py
```

`scripts/scheduling_demo.py` runs the optional facility-wide scheduling
engine (`app/scheduling.py`) against the real seeded Jaipur facility and
prints the whole-day proposed dock schedule. Offline/deterministic -- no
API key needed, needs `ortools` installed (in `requirements.txt`).

```powershell
python scripts\serve.py
```

`scripts/serve.py` runs the REST API + UI (`app/api.py`, `web/index.html`)
with uvicorn. Open `http://127.0.0.1:8000` in a browser: identify by
phone, chat, and optionally load a facility's proposed schedule.
`/identify` and `/schedule/*` are offline/deterministic; `/chat` calls a
real LLM per message, same as `chat.py`. No authentication -- local dev
tool only. For auto-reload while editing `app/api.py`, run
`uvicorn app.api:app --reload` directly instead.

## Automated tests

```powershell
pip install -r requirements-dev.txt
pytest -q
```

Deterministic only -- no LLM calls, runs in well under a second, safe in
CI. Each test gets its own fresh `:memory:` DB (or temp file DB, for the
concurrency test) built from `db/schema_and_seed.sql` -- never touches
`data/dockslot.db`. Run after any change to `app/repository.py` or
`app/conversation.py`'s pure-function helpers.
