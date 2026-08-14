"""Automated version of scripts/concurrency_demo.py's proof: real threads,
real separate connections, synchronized to fire together. Uses a temp
file DB (not :memory:, which can't be shared across connections/threads
without special URI handling, and not data/dockslot.db, which is the
dev's own generated file) so this is a genuine multi-connection test.
"""
import sqlite3
import threading
from pathlib import Path

from app.repository import propose_booking

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = (REPO_ROOT / "db" / "schema_and_seed.sql").read_text(encoding="utf-8")


def _build_temp_db(path):
    connection = sqlite3.connect(str(path))
    connection.executescript(SCHEMA_SQL)
    connection.commit()
    connection.close()


def _connect(path):
    connection = sqlite3.connect(str(path))
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    return connection


def test_concurrent_booking_same_slot_exactly_one_winner(tmp_path):
    db_path = tmp_path / "concurrency_test.db"
    _build_temp_db(db_path)

    shipment_ids = ["SHP1001", "SHP1008", "SHP1012", "SHP1018", "SHP1019"]
    target_slot = "SLOT-JAI-001"
    barrier = threading.Barrier(len(shipment_ids))
    results = [None] * len(shipment_ids)

    def worker(i, shipment_id):
        connection = _connect(db_path)
        barrier.wait()
        results[i] = propose_booking(connection, shipment_id, target_slot)
        connection.close()

    threads = [threading.Thread(target=worker, args=(i, sid)) for i, sid in enumerate(shipment_ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    successes = [r for r in results if r.success]
    assert len(successes) == 1, "exactly one of {} concurrent requests should win the slot".format(len(shipment_ids))

    failures = [r for r in results if not r.success]
    assert all("taken" in r.reason.lower() for r in failures)

    # Prove it from the actual stored rows, not the Python-level results.
    verify_conn = _connect(db_path)
    count = verify_conn.execute(
        """
        SELECT COUNT(*) c FROM appointments
        WHERE slot_id = ? AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
        """,
        (target_slot,),
    ).fetchone()["c"]
    verify_conn.close()
    assert count == 1
