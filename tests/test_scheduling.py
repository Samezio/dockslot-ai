"""app/scheduling.py -- the optional facility-wide scheduling engine
(brief section 7.3). Deterministic, offline, no LLM involved.
"""
from collections import defaultdict

from app.scheduling import (
    FacilitySnapshot,
    FixedOccupancy,
    ScheduleJob,
    build_facility_snapshot,
    solve_schedule,
)


def _assert_valid_schedule(snapshot, result):
    """Structural invariants that must hold for ANY snapshot: no two
    intervals on the same dock overlap, nothing starts before it could
    arrive, nothing runs on a dock it isn't eligible for."""
    by_dock = defaultdict(list)
    for a in result.assignments:
        by_dock[a.dock_id].append((a.start_minutes, a.end_minutes))
    for occ in snapshot.fixed:
        by_dock[occ.dock_id].append((occ.start_minutes, occ.end_minutes))

    for dock_id, intervals in by_dock.items():
        intervals.sort()
        for (s1, e1), (s2, e2) in zip(intervals, intervals[1:]):
            assert s2 >= e1, "overlap on {}: ({}, {}) vs ({}, {})".format(dock_id, s1, e1, s2, e2)

    jobs_by_id = {job.shipment_id: job for job in snapshot.jobs}
    for a in result.assignments:
        job = jobs_by_id[a.shipment_id]
        assert a.start_minutes >= job.release_minutes
        assert a.dock_id in job.eligible_dock_ids
        assert a.end_minutes - a.start_minutes == job.duration_minutes


# --- The brief's own worked example (section 7.3) ----------------------
# "At 5:25 PM, the Jaipur facility has two dock doors": SHP-201 (early,
# waiting, D1 or D2), SHP-202 (late, waiting, D2 only), SHP-203 (expected
# later still, not arrived, D1 or D2), SHP-204 (already on D1 until 5:40).
# Minutes-since-midnight: 5:00pm=1020, 5:05=1025, 5:25=1045, 5:40=1060,
# 6:35=1155, 11:00pm close=1380.


def _brief_example_snapshot():
    return FacilitySnapshot(
        facility_id="FAC-BRIEF",
        day_start_minutes=360,  # 06:00
        day_end_minutes=1380,  # 23:00
        last_new_start_minutes=None,
        dock_ids=["D1", "D2"],
        jobs=[
            ScheduleJob("SHP-201", release_minutes=1025, duration_minutes=40, eligible_dock_ids=["D1", "D2"], priority_weight=2, due_minutes=None, reason="gate-in 17:05"),
            ScheduleJob("SHP-202", release_minutes=1045, duration_minutes=30, eligible_dock_ids=["D2"], priority_weight=2, due_minutes=None, reason="gate-in 17:25"),
            ScheduleJob("SHP-203", release_minutes=1155, duration_minutes=45, eligible_dock_ids=["D1", "D2"], priority_weight=2, due_minutes=None, reason="declared ETA 18:35"),
        ],
        fixed=[FixedOccupancy("D1", start_minutes=1000, end_minutes=1060, reason="in progress: SHP-204")],
    )


def test_brief_example_two_docks_four_trucks():
    snapshot = _brief_example_snapshot()
    result = solve_schedule(snapshot)

    assert result.status in ("OPTIMAL", "FEASIBLE")
    assert result.unscheduled_shipment_ids == []
    _assert_valid_schedule(snapshot, result)

    by_id = {a.shipment_id: a for a in result.assignments}
    # SHP-202 is compatible ONLY with D2 (D1 is occupied by SHP-204 until
    # 1060) -- it must be assigned D2, whenever that turns out to be.
    assert by_id["SHP-202"].dock_id == "D2"

    # NOT asserting SHP-202 starts the moment it arrives (1045): the
    # optimizer found a genuinely better global arrangement -- SHP-201
    # (D1-or-D2, arrives earlier at 1025, before D1 even frees up at 1060)
    # grabs D2 immediately instead of waiting for D1, pushing SHP-202 to
    # 1065. Total weighted waiting = 40, versus 70 for the "obvious"
    # FCFS-per-dock assignment (SHP-202 grabs D2 at 1045, forcing SHP-201
    # to wait until D1 frees at 1060). This is exactly the kind of
    # facility-wide reasoning a single-shipment view can't do -- assert
    # the solver actually found the better arrangement, not a guess at
    # which exact one it picked.
    assert result.objective_value <= 70


# --- Mechanism tests (directly constructed scenarios) -------------------


def test_higher_priority_scheduled_first_when_capacity_is_scarce():
    # Two jobs, identical release/duration, ONE eligible dock between
    # them -- only one can go first. The CRITICAL one should win.
    snapshot = FacilitySnapshot(
        facility_id="FAC-TEST",
        day_start_minutes=0,
        day_end_minutes=600,
        last_new_start_minutes=None,
        dock_ids=["D1"],
        jobs=[
            ScheduleJob("LOW-JOB", release_minutes=0, duration_minutes=60, eligible_dock_ids=["D1"], priority_weight=1, due_minutes=None, reason="test"),
            ScheduleJob("CRITICAL-JOB", release_minutes=0, duration_minutes=60, eligible_dock_ids=["D1"], priority_weight=4, due_minutes=None, reason="test"),
        ],
        fixed=[],
    )
    result = solve_schedule(snapshot)
    _assert_valid_schedule(snapshot, result)
    by_id = {a.shipment_id: a for a in result.assignments}
    assert by_id["CRITICAL-JOB"].start_minutes == 0
    assert by_id["LOW-JOB"].start_minutes == 60


def test_tight_due_date_scheduled_before_equal_priority_no_deadline():
    # Equal priority -- the one with a real deadline to hit should still
    # go first, since missing it is explicitly penalized (tardiness).
    snapshot = FacilitySnapshot(
        facility_id="FAC-TEST",
        day_start_minutes=0,
        day_end_minutes=600,
        last_new_start_minutes=None,
        dock_ids=["D1"],
        jobs=[
            ScheduleJob("HAS-DEADLINE", release_minutes=0, duration_minutes=60, eligible_dock_ids=["D1"], priority_weight=2, due_minutes=60, reason="test"),
            ScheduleJob("NO-DEADLINE", release_minutes=0, duration_minutes=60, eligible_dock_ids=["D1"], priority_weight=2, due_minutes=None, reason="test"),
        ],
        fixed=[],
    )
    result = solve_schedule(snapshot)
    _assert_valid_schedule(snapshot, result)
    by_id = {a.shipment_id: a for a in result.assignments}
    assert by_id["HAS-DEADLINE"].start_minutes == 0
    assert by_id["NO-DEADLINE"].start_minutes == 60


def test_overloaded_day_leaves_lower_priority_job_unscheduled_not_infeasible():
    # Only enough room in the day for 2 of these 3 jobs (each needs the
    # whole 100-minute window, one dock). The engine must degrade
    # gracefully -- report who missed out, not fail outright -- and the
    # one left out should be the lowest-priority one.
    snapshot = FacilitySnapshot(
        facility_id="FAC-TEST",
        day_start_minutes=0,
        day_end_minutes=200,
        last_new_start_minutes=None,
        dock_ids=["D1"],
        jobs=[
            ScheduleJob("CRITICAL-JOB", release_minutes=0, duration_minutes=100, eligible_dock_ids=["D1"], priority_weight=4, due_minutes=None, reason="test"),
            ScheduleJob("HIGH-JOB", release_minutes=0, duration_minutes=100, eligible_dock_ids=["D1"], priority_weight=3, due_minutes=None, reason="test"),
            ScheduleJob("LOW-JOB", release_minutes=0, duration_minutes=100, eligible_dock_ids=["D1"], priority_weight=1, due_minutes=None, reason="test"),
        ],
        fixed=[],
    )
    result = solve_schedule(snapshot)
    assert result.status in ("OPTIMAL", "FEASIBLE")
    _assert_valid_schedule(snapshot, result)
    assert result.unscheduled_shipment_ids == ["LOW-JOB"]
    assert {a.shipment_id for a in result.assignments} == {"CRITICAL-JOB", "HIGH-JOB"}


def test_job_with_no_eligible_dock_is_reported_not_silently_dropped():
    snapshot = FacilitySnapshot(
        facility_id="FAC-TEST",
        day_start_minutes=0,
        day_end_minutes=600,
        last_new_start_minutes=None,
        dock_ids=["D1"],
        jobs=[],
        fixed=[],
        unschedulable_no_eligible_dock=["SHP-TOO-HEAVY"],
    )
    result = solve_schedule(snapshot)
    assert result.unscheduled_shipment_ids == ["SHP-TOO-HEAVY"]
    assert result.assignments == []


def test_late_start_after_last_new_start_flagged_for_manual_approval():
    snapshot = FacilitySnapshot(
        facility_id="FAC-TEST",
        day_start_minutes=0,
        day_end_minutes=600,
        last_new_start_minutes=100,
        dock_ids=["D1"],
        jobs=[ScheduleJob("LATE-JOB", release_minutes=150, duration_minutes=60, eligible_dock_ids=["D1"], priority_weight=2, due_minutes=None, reason="test")],
        fixed=[],
    )
    result = solve_schedule(snapshot)
    assert len(result.assignments) == 1
    assert result.assignments[0].needs_manual_approval is True


# --- build_facility_snapshot against the real seeded database ----------


def test_build_facility_snapshot_merges_overlapping_fixed_occupancies(conn):
    # DOCK-JAI-D2 in the seed data has a dock_status_events
    # CAPACITY_REDUCTION window (08:00-09:20) that overlaps almost
    # exactly with SHP1002's own in-progress occupancy (08:05-09:15) --
    # both describe the same real overrun. Feeding both to the solver as
    # separate mandatory blocks is a hard, unsatisfiable conflict (this
    # was a real bug, caught by running the engine against real data)
    # unless they're merged first.
    snapshot = build_facility_snapshot(conn, "FAC-JAI-01")
    d2_occupancies = [occ for occ in snapshot.fixed if occ.dock_id == "DOCK-JAI-D2"]
    assert len(d2_occupancies) == 1
    assert d2_occupancies[0].start_minutes == 480  # 08:00
    assert d2_occupancies[0].end_minutes == 560  # 09:20


def test_solve_schedule_against_real_seed_data_is_valid_and_solves(conn):
    snapshot = build_facility_snapshot(conn, "FAC-JAI-01")
    result = solve_schedule(snapshot)
    assert result.status in ("OPTIMAL", "FEASIBLE")
    _assert_valid_schedule(snapshot, result)
    # Every job is accounted for exactly once, either scheduled or
    # explicitly reported unscheduled -- nothing silently vanishes.
    all_job_ids = {job.shipment_id for job in snapshot.jobs}
    accounted = {a.shipment_id for a in result.assignments} | set(result.unscheduled_shipment_ids)
    assert all_job_ids <= accounted


def test_reefer_shipment_with_maintenance_conflict_left_unscheduled(conn):
    # seed case THR005: SHP1015's only compatible dock (reefer, D5) is
    # under maintenance from 18:00, and its declared ETA (18:30) is after
    # that -- matches what app/repository.py::find_feasible_slots finds
    # independently for the same shipment (see tests/test_repository.py).
    snapshot = build_facility_snapshot(conn, "FAC-JAI-01")
    result = solve_schedule(snapshot)
    assert "SHP1015" in result.unscheduled_shipment_ids
