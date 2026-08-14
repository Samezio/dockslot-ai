"""Facility-wide dock scheduling engine (brief section 7.3 -- optional).

What this is: a deterministic, explainable tool that looks at ALL trucks
relevant to one facility at once (already-in-a-dock, waiting in the yard,
still in transit) and proposes a dock assignment for every truck that
still needs one -- the brief's own example (SHP201-204 competing for two
dock doors) is exactly this problem.

What this is NOT: it never reads free text and never decides what a
driver meant. It takes a structured snapshot (build_facility_snapshot)
and returns a structured result (solve_schedule) -- the same
tool-boundary discipline as app/repository.py's functions, just scoped
to a whole facility instead of one shipment. Nothing here is wired into
app/conversation.py's per-message flow: a driver's own request is still
answered by app/repository.py::find_feasible_slots, which only needs
that one shipment's view. This module is for the separate, optional
facility-wide question ("what should happen to everyone waiting right
now"), called on demand (see scripts/scheduling_demo.py).

Modeling: this is the classic "unrelated parallel machines with
eligibility" scheduling problem -- docks are machines, shipments are
jobs, and a job may only run on machines (docks) it's physically
compatible with. Solved with Google OR-Tools CP-SAT
(ortools.sat.python.cp_model), the tool the brief itself points to
(docs/developer/architecture.md links the references). The standard
CP-SAT pattern for this: one optional interval variable per (job,
eligible-dock) pair sharing a common start/end variable, "add_no_overlap"
per dock across all intervals assigned to it (including fixed/blocked
ones), and "exactly one of {assigned to some eligible dock, explicitly
left unscheduled}" per job so an overloaded day degrades to "some trucks
don't get a slot today" instead of the whole model being infeasible.

Data boundary (matches the brief exactly, section 7.3): only the
original planned ETA, the latest driver-declared ETA, and actual gate-in
time once a truck reaches the facility. No live GPS, no assumptions
beyond what's already in the database.
"""
import sqlite3
from dataclasses import dataclass, field
from typing import Dict, List, Optional

from ortools.sat.python import cp_model

# Ordinal weight, not a real-world unit -- just needs to rank CRITICAL >
# HIGH > NORMAL > LOW consistently in the objective (brief: "shipment
# priority: a weight or penalty when waiting or lateness is calculated").
PRIORITY_WEIGHTS = {"CRITICAL": 4, "HIGH": 3, "NORMAL": 2, "LOW": 1}
DEFAULT_PRIORITY_WEIGHT = 2

# Objective tuning, all in the same "weighted minutes" unit as waiting
# time. Large gaps between the tiers are deliberate: the solver should
# always prefer scheduling a job over leaving it unscheduled, and always
# prefer meeting a stated deadline over merely minimizing generic
# waiting -- these aren't tunable business policy, just enough separation
# that the solver's priority ordering never inverts.
UNSCHEDULED_PENALTY = 10_000
TARDINESS_MULTIPLIER = 10

SOLVE_TIME_LIMIT_SECONDS = 10.0


@dataclass
class ScheduleJob:
    shipment_id: str
    release_minutes: int
    duration_minutes: int
    eligible_dock_ids: List[str]
    priority_weight: int
    due_minutes: Optional[int]
    reason: str  # human-readable basis for release_minutes, for explainability


@dataclass
class FixedOccupancy:
    """A dock-time block the solver must route around, never move --
    either a truck already unloading, or a maintenance/breakdown window."""
    dock_id: str
    start_minutes: int
    end_minutes: int
    reason: str


@dataclass
class FacilitySnapshot:
    facility_id: str
    day_start_minutes: int
    day_end_minutes: int
    last_new_start_minutes: Optional[int]
    dock_ids: List[str]
    jobs: List[ScheduleJob]
    fixed: List[FixedOccupancy]
    # Excluded before the solver even runs -- no eligible dock exists at
    # all, or it can't finish within operating hours no matter the dock.
    unschedulable_no_eligible_dock: List[str] = field(default_factory=list)
    unschedulable_cannot_fit_hours: List[str] = field(default_factory=list)


@dataclass
class ScheduledAssignment:
    shipment_id: str
    dock_id: str
    start_minutes: int
    end_minutes: int
    needs_manual_approval: bool  # starts at/after the facility's last-new-start rule


@dataclass
class ScheduleResult:
    status: str
    assignments: List[ScheduledAssignment]
    unscheduled_shipment_ids: List[str]
    objective_value: Optional[float]


def _time_to_minutes(hhmm: str) -> int:
    hours, minutes = hhmm.split(":")
    return int(hours) * 60 + int(minutes)


def _ts_to_minutes(ts: str) -> int:
    """'2026-08-04T11:20:00+05:30' -> minutes since that day's midnight.
    This is a single-operational-day snapshot (see db/schema_and_seed.sql
    header) -- there's no cross-day scheduling to worry about."""
    return _time_to_minutes(ts.split("T")[1][:5])


def build_facility_snapshot(conn: sqlite3.Connection, facility_id: str) -> FacilitySnapshot:
    facility = conn.execute(
        "SELECT open_time, close_time FROM facilities WHERE facility_id = ?", (facility_id,)
    ).fetchone()
    if facility is None:
        raise ValueError("Unknown facility_id: {}".format(facility_id))
    day_start = _time_to_minutes(facility["open_time"])
    day_end = _time_to_minutes(facility["close_time"])

    rule = conn.execute(
        """
        SELECT rule_value FROM facility_rules
        WHERE facility_id = ? AND rule_type = 'LAST_NEW_START_TIME' AND active_flag = 1
        """,
        (facility_id,),
    ).fetchone()
    last_new_start = _time_to_minutes(rule["rule_value"]) if rule else None

    docks = conn.execute(
        """
        SELECT dock_id, dock_type, supports_refrigerated, max_vehicle_weight_kg
        FROM docks WHERE facility_id = ? AND dock_status = 'ACTIVE'
        """,
        (facility_id,),
    ).fetchall()
    dock_ids = [d["dock_id"] for d in docks]

    fixed: List[FixedOccupancy] = []

    in_dock_rows = conn.execute(
        """
        SELECT s.shipment_id, s.expected_unload_min, fc.actual_dock_id, fc.dock_in_ts, fc.unload_end_ts
        FROM shipments s
        JOIN facility_checkins fc ON fc.shipment_id = s.shipment_id
        WHERE s.destination_facility_id = ? AND fc.queue_state = 'IN_DOCK' AND fc.actual_dock_id IS NOT NULL
        """,
        (facility_id,),
    ).fetchall()
    for row in in_dock_rows:
        start = _ts_to_minutes(row["dock_in_ts"])
        end = _ts_to_minutes(row["unload_end_ts"]) if row["unload_end_ts"] else start + row["expected_unload_min"]
        fixed.append(FixedOccupancy(row["actual_dock_id"], start, end, "in progress: {}".format(row["shipment_id"])))
    in_dock_shipment_ids = {row["shipment_id"] for row in in_dock_rows}

    if dock_ids:
        placeholders = ",".join("?" for _ in dock_ids)
        for row in conn.execute(
            """
            SELECT dock_id, event_type, event_start_ts, event_end_ts FROM dock_status_events
            WHERE dock_id IN ({}) AND event_end_ts IS NOT NULL
            """.format(placeholders),
            dock_ids,
        ):
            fixed.append(
                FixedOccupancy(row["dock_id"], _ts_to_minutes(row["event_start_ts"]), _ts_to_minutes(row["event_end_ts"]), row["event_type"])
            )

    jobs: List[ScheduleJob] = []
    no_eligible: List[str] = []
    cannot_fit: List[str] = []

    rows = conn.execute(
        "SELECT * FROM v_inbound_operational_state WHERE destination_facility_id = ?", (facility_id,)
    ).fetchall()

    for row in rows:
        shipment_id = row["shipment_id"]
        if row["current_status"] in ("COMPLETED", "CANCELLED") or shipment_id in in_dock_shipment_ids:
            continue

        eligible = [
            d["dock_id"]
            for d in docks
            if (row["required_dock_type"] == "ANY" or d["dock_type"] == row["required_dock_type"])
            and d["max_vehicle_weight_kg"] >= row["load_weight_kg"]
            and (not row["temperature_control_required"] or d["supports_refrigerated"])
        ]
        if not eligible:
            no_eligible.append(shipment_id)
            continue

        if row["gate_in_ts"]:
            release = _ts_to_minutes(row["gate_in_ts"])
            reason = "already at facility (gate-in {})".format(row["gate_in_ts"].split("T")[1][:5])
        else:
            release = _ts_to_minutes(row["effective_eta_ts"])
            reason = "declared ETA {}".format(row["effective_eta_ts"].split("T")[1][:5])
        release = max(release, day_start)

        duration = row["expected_unload_min"]
        if release + duration > day_end:
            cannot_fit.append(shipment_id)
            continue

        due = None
        appt = conn.execute(
            """
            SELECT sl.slot_end_ts FROM appointments a
            JOIN appointment_slots sl ON sl.slot_id = a.slot_id
            WHERE a.shipment_id = ? AND a.is_current = 1
              AND a.appointment_status IN ('PENDING_CONFIRMATION', 'CONFIRMED')
            """,
            (shipment_id,),
        ).fetchone()
        if appt:
            due = _ts_to_minutes(appt["slot_end_ts"])
        else:
            exc = conn.execute(
                """
                SELECT latest_acceptable_ts FROM driver_exceptions
                WHERE shipment_id = ? AND latest_acceptable_ts IS NOT NULL
                ORDER BY reported_at DESC, rowid DESC LIMIT 1
                """,
                (shipment_id,),
            ).fetchone()
            if exc:
                due = _ts_to_minutes(exc["latest_acceptable_ts"])

        jobs.append(
            ScheduleJob(
                shipment_id=shipment_id,
                release_minutes=release,
                duration_minutes=duration,
                eligible_dock_ids=eligible,
                priority_weight=PRIORITY_WEIGHTS.get(row["priority_code"], DEFAULT_PRIORITY_WEIGHT),
                due_minutes=due,
                reason=reason,
            )
        )

    return FacilitySnapshot(
        facility_id=facility_id,
        day_start_minutes=day_start,
        day_end_minutes=day_end,
        last_new_start_minutes=last_new_start,
        dock_ids=dock_ids,
        jobs=jobs,
        fixed=_merge_overlapping_occupancies(fixed),
        unschedulable_no_eligible_dock=no_eligible,
        unschedulable_cannot_fit_hours=cannot_fit,
    )


def _merge_overlapping_occupancies(occupancies: List[FixedOccupancy]) -> List[FixedOccupancy]:
    """Two independent data sources can describe the same real-world
    unavailability -- e.g. a dock_status_events CAPACITY_REDUCTION window
    that exists precisely BECAUSE a truck is still in that dock past its
    expected time (seen in the seed data: DOCK-JAI-D2's overrun event and
    SHP1002's own in-progress occupancy overlap almost exactly). Feeding
    two overlapping MANDATORY intervals to the same dock's add_no_overlap
    is a hard, unsatisfiable conflict regardless of any job -- merge
    overlapping/touching occupancies per dock into one block first."""
    merged: List[FixedOccupancy] = []
    by_dock: Dict[str, List[FixedOccupancy]] = {}
    for occ in occupancies:
        by_dock.setdefault(occ.dock_id, []).append(occ)

    for dock_id, dock_occupancies in by_dock.items():
        for occ in sorted(dock_occupancies, key=lambda o: o.start_minutes):
            if merged and merged[-1].dock_id == dock_id and occ.start_minutes <= merged[-1].end_minutes:
                last = merged[-1]
                merged[-1] = FixedOccupancy(
                    dock_id=dock_id,
                    start_minutes=last.start_minutes,
                    end_minutes=max(last.end_minutes, occ.end_minutes),
                    reason="{} + {}".format(last.reason, occ.reason),
                )
            else:
                merged.append(occ)
    return merged


def solve_schedule(snapshot: FacilitySnapshot) -> ScheduleResult:
    model = cp_model.CpModel()
    day_end = snapshot.day_end_minutes

    dock_intervals: Dict[str, list] = {dock_id: [] for dock_id in snapshot.dock_ids}
    for i, occ in enumerate(snapshot.fixed):
        interval = model.new_interval_var(
            occ.start_minutes, occ.end_minutes - occ.start_minutes, occ.end_minutes, "fixed_{}".format(i)
        )
        dock_intervals.setdefault(occ.dock_id, []).append(interval)

    start_vars = {}
    end_vars = {}
    presence_vars = {}
    unscheduled_vars = {}
    objective_terms = []

    for job in snapshot.jobs:
        start = model.new_int_var(job.release_minutes, day_end - job.duration_minutes, "start_{}".format(job.shipment_id))
        end = model.new_int_var(job.release_minutes + job.duration_minutes, day_end, "end_{}".format(job.shipment_id))
        model.add(end == start + job.duration_minutes)
        start_vars[job.shipment_id] = start
        end_vars[job.shipment_id] = end

        presences = []
        for dock_id in job.eligible_dock_ids:
            presence = model.new_bool_var("presence_{}_{}".format(job.shipment_id, dock_id))
            interval = model.new_optional_interval_var(
                start, job.duration_minutes, end, presence, "interval_{}_{}".format(job.shipment_id, dock_id)
            )
            dock_intervals[dock_id].append(interval)
            presences.append(presence)
            presence_vars[(job.shipment_id, dock_id)] = presence

        # Exactly one of: assigned to one of its eligible docks, or
        # explicitly left unscheduled. This is what keeps an overloaded
        # day solvable -- the alternative (forcing every job onto some
        # dock) would make the whole model infeasible the moment demand
        # exceeds capacity, instead of gracefully reporting who missed out.
        unscheduled = model.new_bool_var("unscheduled_{}".format(job.shipment_id))
        model.add(sum(presences) + unscheduled == 1)
        unscheduled_vars[job.shipment_id] = unscheduled

        objective_terms.append(job.priority_weight * (start - job.release_minutes))
        objective_terms.append(UNSCHEDULED_PENALTY * job.priority_weight * unscheduled)

        if job.due_minutes is not None:
            tardy = model.new_int_var(0, day_end, "tardy_{}".format(job.shipment_id))
            model.add(tardy >= end - job.due_minutes)
            objective_terms.append(TARDINESS_MULTIPLIER * job.priority_weight * tardy)

    for intervals in dock_intervals.values():
        if len(intervals) > 1:
            model.add_no_overlap(intervals)

    model.minimize(sum(objective_terms))

    solver = cp_model.CpSolver()
    solver.parameters.max_time_in_seconds = SOLVE_TIME_LIMIT_SECONDS
    status = solver.solve(model)
    status_name = solver.status_name(status)

    unscheduled_ids = list(snapshot.unschedulable_no_eligible_dock) + list(snapshot.unschedulable_cannot_fit_hours)
    assignments: List[ScheduledAssignment] = []
    objective_value = None

    if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
        objective_value = solver.objective_value
        for job in snapshot.jobs:
            if solver.value(unscheduled_vars[job.shipment_id]):
                unscheduled_ids.append(job.shipment_id)
                continue
            for dock_id in job.eligible_dock_ids:
                if solver.value(presence_vars[(job.shipment_id, dock_id)]):
                    start_m = solver.value(start_vars[job.shipment_id])
                    end_m = solver.value(end_vars[job.shipment_id])
                    needs_manual = snapshot.last_new_start_minutes is not None and start_m > snapshot.last_new_start_minutes
                    assignments.append(ScheduledAssignment(job.shipment_id, dock_id, start_m, end_m, needs_manual))
                    break
    else:
        # The "everyone unscheduled" solution is always feasible given the
        # sum(presences) + unscheduled == 1 constraint, so a real solver-
        # level INFEASIBLE/UNKNOWN here would point to a modeling bug, not
        # an overloaded day -- surface it as "nobody scheduled" rather
        # than silently returning an empty, misleadingly-OK-looking result.
        unscheduled_ids.extend(job.shipment_id for job in snapshot.jobs)

    return ScheduleResult(
        status=status_name,
        assignments=assignments,
        unscheduled_shipment_ids=unscheduled_ids,
        objective_value=objective_value,
    )
