# Setup and run

From a fresh clone to a working chat in about five minutes.

Commands are PowerShell (Windows). On macOS/Linux use forward slashes and
`source .venv/bin/activate`.

---

## 1. Python

The project targets **Python 3.9.6**.

```powershell
py -0p          # list installed interpreters
py -3.9 -m venv .venv
.venv\Scripts\Activate.ps1
```

No `py -3.9`? Install Python 3.9 first. Don't build the venv on a newer
interpreter and hope nothing 3.10-only creeps in.

You should see `(.venv)` at the start of your prompt from here on.

## 2. Install dependencies

```powershell
pip install -r requirements.txt
```

Add the dev extras if you want to run the tests:

```powershell
pip install -r requirements-dev.txt
```

You may see a `FutureWarning` about Python 3.9 being end-of-life, from
Google's libraries. It's a warning, not an error.

## 3. Populate the database

```powershell
python scripts\build_db.py
```

That's the data step. It creates `data\dockslot.db` from the tracked
`db\schema_and_seed.sql` — 15 drivers, 21 shipments, 6 facilities, docks,
slots, appointments and seeded chat threads.

Safe to re-run **any time**. It deletes and rebuilds the database from
scratch, which is the quickest way to undo anything a demo did. Run it
before a demo so you start from a known state.

Never hand-edit `data\dockslot.db` — it's generated and gitignored. To
change the data, edit `db\schema_and_seed.sql` and rebuild.

## 4. Configure an LLM provider

Only the chat needs an LLM. Everything else runs offline.

```powershell
copy .env.example .env
```

Open `.env` and set `LLM_PROVIDER` plus that provider's key:

| `LLM_PROVIDER` | Needs | Notes |
|---|---|---|
| `google_genai` | `GOOGLE_API_KEY` | Default. Gemini. |
| `aicredit` | `AICREDIT_API_KEY` + `AICREDIT_BASE_URL` | OpenAI-compatible gateway. ~2-4 s per message. |
| `openai` | `OPENAI_API_KEY` | |
| `openrouter` | `OPEN_ROUNTER_API_KEY` | Capped at 300 max tokens as a dev-cost guard. |
| `ollama` | nothing — runs locally | See the warning below. |

`.env` is gitignored. Never commit real keys.

> **Don't use `ollama` for a demo.** A local `qwen3:8b` takes **2-5
> minutes per message** on CPU, and the UI shows no progress while it
> waits, so it looks like nothing is happening. It's for offline
> iteration only.

## 5. Run it

```powershell
python scripts\serve.py
```

Open **http://127.0.0.1:8000**.

- Enter a phone number to identify yourself. Seeded example:
  **`9000010010`** (Deepak Saini, DRV010 — one shipment, clean history).
- Then chat: *"Stuck in traffic, I'll reach around 15:30. Any later
  slots?"* → it offers options → *"the first one please"* → it books.
- The dispatcher's facility-wide schedule is a separate page:
  **http://127.0.0.1:8000/ops**

To stop it: `Ctrl+C`.

---

## Other ways to run it

All of these are offline and need no API key, except `chat.py` and
`chat_demo.py`.

```powershell
python scripts\demo.py               # feasibility, ambiguity, escalation, a booking race
python scripts\concurrency_demo.py   # 5 threads race for 1 slot -> exactly 1 winner
python scripts\scheduling_demo.py    # whole-day facility schedule proposal
python scripts\chat.py               # same chat, but in the terminal (needs an LLM)
```

Tests (deterministic, no LLM calls, ~3 seconds):

```powershell
pytest -q
```

---

## Useful phone numbers

Identification is by phone, like a real messaging channel would do. There
is no default driver.

| Phone | Driver | Why it's interesting |
|---|---|---|
| `9000010010` | DRV010 | **Best for a first try.** One shipment, no prior conversation. |
| `9000010012` | DRV012 | No current appointment — books a fresh slot. |
| `9000010015` | DRV015 | Refrigerated load, only reefer dock is down → escalates to a human. |
| `9000010006` | DRV006 | Has **two** shipments — say "SHP1006" to pick one. |

Full list: `SELECT driver_id, driver_name, phone FROM drivers;`

---

## If something goes wrong

**`only one usage of each socket address ... 8000`**
Something is already on the port — usually a server you left running.

```powershell
Get-NetTCPConnection -LocalPort 8000 -State Listen | Select-Object OwningProcess
Stop-Process -Id <pid> -Force
```

**`data\dockslot.db does not exist yet`**
You skipped step 3. Run `python scripts\build_db.py`.

**`ModuleNotFoundError: No module named 'app'`**
Run scripts from the repo root (`cd` into it first), not from `scripts\`.

**The chat hangs / never replies**
Almost certainly `LLM_PROVIDER=ollama`. Check `.env` — see step 4.

**HTTP 500 on a chat message**
The LLM provider rejected the call: missing key, no credits, or wrong
base URL. Check the terminal running `serve.py` for the real error.

**Chat replies but the answers look wrong**
Check which provider you're on. Small local models produce noticeably
worse structured output than hosted ones.

---

## Where to look next

- [SUBMISSION.md](SUBMISSION.md) — the challenge write-up
- [developer/architecture.md](developer/architecture.md) — how it works
- [developer/known-issues/](developer/known-issues/README.md) — confirmed bugs, with reproductions
- [developer/development.md](developer/development.md) — deeper development notes
