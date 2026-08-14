"""Shared pytest fixtures.

Tests never touch data/dockslot.db (the dev DB) -- each test gets its own
fresh :memory: database built from the same tracked db/schema_and_seed.sql
that scripts/build_db.py uses, so tests exercise the real schema/seed data
without any file-system side effects or ordering dependencies between tests.
"""
import sqlite3
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

SCHEMA_SQL = (REPO_ROOT / "db" / "schema_and_seed.sql").read_text(encoding="utf-8")


@pytest.fixture
def conn():
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    connection.executescript(SCHEMA_SQL)
    connection.execute("PRAGMA foreign_keys = ON;")
    yield connection
    connection.close()
