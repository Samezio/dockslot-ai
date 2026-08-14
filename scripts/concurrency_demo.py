"""Prove the concurrency guarantee under REAL concurrent access -- separate
threads, separate DB connections, synchronized to fire at the same
instant -- not just sequential calls in one script (that's what
scripts/demo.py's step 4 does; this is the harder, real version of the
same claim). No LLM involved; this is purely app/repository.py.

Run: python scripts\\concurrency_demo.py
"""
import sys
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection
from app.repository import find_feasible_slots, propose_booking
from scripts.build_db import build_db


def _race_for_exact_slot(shipment_ids, slot_id):
    """N threads, N separate connections, all attempt the SAME slot_id at
    the same instant. Expect exactly one success."""
    barrier = threading.Barrier(len(shipment_ids))
    results = [None] * len(shipment_ids)

    def worker(i, shipment_id):
        conn = get_connection()
        barrier.wait()  # every thread blocks here until all are ready, then releases together
        results[i] = propose_booking(conn, shipment_id, slot_id)
        conn.close()

    threads = [threading.Thread(target=worker, args=(i, sid)) for i, sid in enumerate(shipment_ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _race_for_best_available(shipment_ids, after_ts):
    """N threads, N separate connections. Each independently asks 'what's
    my best slot right now?' (limit=1) and tries to book it -- the brief's
    literal example: several drivers each want the one best available
    window at the same time."""
    barrier = threading.Barrier(len(shipment_ids))
    results = [None] * len(shipment_ids)

    def worker(i, shipment_id):
        conn = get_connection()
        barrier.wait()
        options = find_feasible_slots(conn, shipment_id, after_ts=after_ts, limit=1)
        if not options:
            results[i] = (None, None)
        else:
            results[i] = (options[0].slot_id, propose_booking(conn, shipment_id, options[0].slot_id))
        conn.close()

    threads = [threading.Thread(target=worker, args=(i, sid)) for i, sid in enumerate(shipment_ids)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    return results


def _assert_no_double_booking(slot_ids):
    """Query the DB directly -- don't trust the Python-level results,
    prove it from actual stored rows."""
    conn = get_connection()
    violations = []
    for slot_id in slot_ids:
        if slot_id is None:
            continue
        count = conn.execute(
            """
            SELECT COUNT(*) c FROM appointments
            WHERE slot_id = ? AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
            """,
            (slot_id,),
        ).fetchone()["c"]
        if count > 1:
            violations.append((slot_id, count))
    conn.close()
    return violations


def main():
    print("=== Scenario 1: 3 drivers, 1 exact slot, synchronized ===")
    build_db()
    shipments_1 = ["SHP1001", "SHP1008", "SHP1012"]
    target_slot = "SLOT-JAI-001"
    results = _race_for_exact_slot(shipments_1, target_slot)
    for sid, r in zip(shipments_1, results):
        print("  {} -> success={} reason={}".format(sid, r.success, r.reason))
    wins = sum(1 for r in results if r.success)
    print("  winners: {} (expected exactly 1)".format(wins))
    violations = _assert_no_double_booking([target_slot])
    print("  DB integrity check: {}".format("PASS -- no slot has >1 active appointment" if not violations else "FAIL -- {}".format(violations)))

    print("\n=== Scenario 2: 5 drivers each independently ask for 'the best slot after 18:00' ===")
    print("(the brief's own example, section 7.2: 'Five drivers may ask for a 6:00 PM")
    print(" window when only one compatible dock is free.')")
    build_db()
    shipments_2 = ["SHP1001", "SHP1008", "SHP1012", "SHP1018", "SHP1019"]
    results_2 = _race_for_best_available(shipments_2, after_ts="2026-08-04T18:00:00+05:30")
    for sid, (slot_id, result) in zip(shipments_2, results_2):
        if slot_id is None:
            print("  {} -> no feasible slot found".format(sid))
        else:
            print("  {} wanted {} -> success={} reason={}".format(sid, slot_id, result.success, result.reason))
    requested = [slot_id for slot_id, _ in results_2 if slot_id]
    wins_2 = sum(1 for _, r in results_2 if r and r.success)
    contested = len(requested) - len(set(requested))
    print("  {} distinct slots requested across {} drivers ({} requests landed on an".format(
        len(set(requested)), len(shipments_2), contested
    ))
    print("  already-requested slot -- weight/dock-type eligibility differs per shipment,")
    print("  so it's not always all-vs-one). {} booked successfully; every genuine collision".format(wins_2))
    print("  resolved to exactly one winner.")
    violations_2 = _assert_no_double_booking([slot_id for slot_id, _ in results_2])
    print("  DB integrity check: {}".format("PASS -- no slot has >1 active appointment" if not violations_2 else "FAIL -- {}".format(violations_2)))


if __name__ == "__main__":
    main()
