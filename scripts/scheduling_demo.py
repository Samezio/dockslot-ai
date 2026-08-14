"""Narrated walkthrough of the facility-wide scheduling engine
(app/scheduling.py, brief section 7.3 -- optional). Offline/deterministic
-- no LLM, no network. Prints Jaipur's whole-day proposed schedule from
the real seeded data, then re-verifies it holds no overlaps.

Run: python scripts\\build_db.py && python scripts\\scheduling_demo.py
"""
import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection
from app.scheduling import build_facility_snapshot, solve_schedule


def _fmt(minutes):
    return "{:02d}:{:02d}".format(minutes // 60, minutes % 60)


def main():
    conn = get_connection()
    facility_id = "FAC-JAI-01"
    snapshot = build_facility_snapshot(conn, facility_id)

    print("=== Facility snapshot: {} ===".format(facility_id))
    print("Operating hours: {} - {}".format(_fmt(snapshot.day_start_minutes), _fmt(snapshot.day_end_minutes)))
    if snapshot.last_new_start_minutes is not None:
        print("Last new start without manual approval: {}".format(_fmt(snapshot.last_new_start_minutes)))
    print("Docks: {}".format(", ".join(snapshot.dock_ids)))

    print("\nFixed occupancies (already committed, never moved):")
    for occ in sorted(snapshot.fixed, key=lambda o: o.start_minutes):
        print("  {} {} - {}  ({})".format(occ.dock_id, _fmt(occ.start_minutes), _fmt(occ.end_minutes), occ.reason))

    if snapshot.unschedulable_no_eligible_dock:
        print("\nExcluded up front (no compatible active dock exists):")
        for shipment_id in snapshot.unschedulable_no_eligible_dock:
            print("  {}".format(shipment_id))
    if snapshot.unschedulable_cannot_fit_hours:
        print("\nExcluded up front (can't finish within operating hours regardless of dock):")
        for shipment_id in snapshot.unschedulable_cannot_fit_hours:
            print("  {}".format(shipment_id))

    print("\n{} schedulable trucks competing for {} docks.".format(len(snapshot.jobs), len(snapshot.dock_ids)))

    result = solve_schedule(snapshot)
    print("\n=== Proposed schedule -- status: {} (objective: {}) ===".format(result.status, result.objective_value))

    for dock_id in snapshot.dock_ids:
        print("\n{}:".format(dock_id))
        items = [(o.start_minutes, o.end_minutes, "[FIXED] {}".format(o.reason)) for o in snapshot.fixed if o.dock_id == dock_id]
        items += [
            (a.start_minutes, a.end_minutes, "{}{}".format(a.shipment_id, " (needs manual approval -- late start)" if a.needs_manual_approval else ""))
            for a in result.assignments
            if a.dock_id == dock_id
        ]
        if not items:
            print("  (nothing scheduled)")
        for start, end, label in sorted(items):
            print("  {} - {}  {}".format(_fmt(start), _fmt(end), label))

    if result.unscheduled_shipment_ids:
        print("\nCould not be scheduled today -- needs operations review:")
        for shipment_id in result.unscheduled_shipment_ids:
            print("  {}".format(shipment_id))

    # Re-verify structurally, the same way tests/test_scheduling.py does --
    # trust but verify, don't just print and hope.
    by_dock = defaultdict(list)
    for a in result.assignments:
        by_dock[a.dock_id].append((a.start_minutes, a.end_minutes))
    for occ in snapshot.fixed:
        by_dock[occ.dock_id].append((occ.start_minutes, occ.end_minutes))
    violations = []
    for dock_id, intervals in by_dock.items():
        intervals.sort()
        for (s1, e1), (s2, e2) in zip(intervals, intervals[1:]):
            if s2 < e1:
                violations.append(dock_id)
    print("\nNo-overlap check: {}".format("PASS" if not violations else "FAIL on {}".format(violations)))

    conn.close()


if __name__ == "__main__":
    main()
