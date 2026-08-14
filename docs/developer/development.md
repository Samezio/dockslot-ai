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

`requirements.txt` is currently empty on purpose -- the deterministic
layer only uses the standard library. Dependencies get added when the
conversational layer needs them.

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
after any change to `app/repository.py` as a quick sanity check.
