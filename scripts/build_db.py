"""Materialize the SQLite database from the versioned schema + seed script.

Usage:
    python scripts/build_db.py

Safe to re-run: it always rebuilds data/dockslot.db from scratch so the
database matches whatever is checked into db/schema_and_seed.sql.
"""
import sqlite3
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = REPO_ROOT / "db" / "schema_and_seed.sql"
DB_PATH = REPO_ROOT / "data" / "dockslot.db"


def build_db():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    if DB_PATH.exists():
        DB_PATH.unlink()

    schema_sql = SCHEMA_PATH.read_text(encoding="utf-8")

    conn = sqlite3.connect(str(DB_PATH))
    try:
        # Foreign keys stay off for the bulk load: the seed script creates
        # tables in an order where earlier tables (e.g. appointment_slots)
        # reference later ones (e.g. docks). Runtime connections enable
        # foreign_keys themselves (see app/db.py).
        conn.executescript(schema_sql)
        conn.commit()
    finally:
        conn.close()

    print("Built {} from {}".format(DB_PATH, SCHEMA_PATH))


if __name__ == "__main__":
    build_db()
