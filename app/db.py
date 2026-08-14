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
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn
