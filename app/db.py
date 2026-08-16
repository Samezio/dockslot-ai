"""SQLite connection helper.

Kept deliberately tiny: one function that returns a connection configured
the way the rest of the app expects (row access by column name, foreign
keys enforced). No ORM — the schema in db/schema_and_seed.sql is the
source of truth and the queries in app/repository.py talk to it directly.
"""
import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "dockslot.db"


def get_connection():
    if not DB_PATH.exists():
        raise FileNotFoundError(
            "{} does not exist yet. Run `python scripts/build_db.py` first.".format(DB_PATH)
        )
    # check_same_thread=False: FastAPI resolves the get_db dependency and
    # runs the route handler as two separate calls into its threadpool,
    # which anyio doesn't guarantee land on the same worker thread -- under
    # concurrent requests they sometimes don't, and sqlite3 raises
    # ProgrammingError by default if a connection crosses threads. Safe to
    # relax here: each request still gets its own connection (see this
    # function's docstring), so there's still exactly one thread touching
    # it at a time -- just not always the same *named* thread across the
    # life of that one connection.
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
