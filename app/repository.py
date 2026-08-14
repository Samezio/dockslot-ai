"""Deterministic business logic: shipment lookup, slot feasibility, booking.

This module is the "operational layer" the brief keeps separate from the
conversational layer (CLAUDE.md section 6). Nothing in here calls an LLM.
Every function takes structured inputs and returns structured outputs, so
an agent (or a human, or a test) can call it the same way.

The concurrency guarantee comes from the database itself
(ux_active_appointment_per_slot / ux_current_active_appointment_per_shipment
in db/schema_and_seed.sql), not from anything in Python: propose_booking()
just attempts the write and reports whether SQLite accepted it.
"""
import sqlite3
import uuid
from datetime import datetime, timedelta, timezone
from typing import List, Optional

from app.models import BookingResult, ShipmentSummary, SlotOption

# Fixed +05:30 offset rather than zoneinfo("Asia/Kolkata"): matches the
# timestamp style already used throughout db/schema_and_seed.sql, and
# avoids depending on the OS tzdata package being installed (it isn't,
# on a stock Windows Python install).
IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_iso() -> str:
    return datetime.now(IST).isoformat(timespec="seconds")


def _row_to_shipment_summary(row: sqlite3.Row) -> ShipmentSummary:
    return ShipmentSummary(
        shipment_id=row["shipment_id"],
        driver_id=row["driver_id"],
        destination_facility_id=row["destination_facility_id"],
        required_dock_type=row["required_dock_type"],
        temperature_control_required=bool(row["temperature_control_required"]),
        load_weight_kg=row["load_weight_kg"],
        expected_unload_min=row["expected_unload_min"],
        priority_code=row["priority_code"],
        current_status=row["current_status"],
        effective_eta_ts=row["effective_eta_ts"],
        eta_source=row["eta_source"],
        eta_confidence=row["eta_confidence"],
        current_appointment_id=row["appointment_id"],
        current_slot_id=row["slot_id"],
        current_slot_start_ts=row["slot_start_ts"],
        current_slot_end_ts=row["slot_end_ts"],
    )


def find_shipments_for_driver(conn: sqlite3.Connection, driver_id: str) -> List[ShipmentSummary]:
    """Active shipments for a driver, most imminent first.

    A driver can be assigned more than one shipment on the same day (see
    seed case DRV004 / THR010) -- callers must handle a list of length > 1
    by asking the driver to disambiguate, not by guessing.
    """
    rows = conn.execute(
        """
        SELECT * FROM v_inbound_operational_state
        WHERE driver_id = ?
          AND current_status NOT IN ('COMPLETED', 'CANCELLED')
        ORDER BY effective_eta_ts
        """,
        (driver_id,),
    ).fetchall()
    return [_row_to_shipment_summary(r) for r in rows]


def get_shipment(conn: sqlite3.Connection, shipment_id: str) -> Optional[ShipmentSummary]:
    row = conn.execute(
        "SELECT * FROM v_inbound_operational_state WHERE shipment_id = ?",
        (shipment_id,),
    ).fetchone()
    return _row_to_shipment_summary(row) if row else None


def _last_new_start_time(conn: sqlite3.Connection, facility_id: str) -> Optional[str]:
    row = conn.execute(
        """
        SELECT rule_value FROM facility_rules
        WHERE facility_id = ? AND rule_type = 'LAST_NEW_START_TIME' AND active_flag = 1
        """,
        (facility_id,),
    ).fetchone()
    return row["rule_value"] if row else None


def _slot_duration_min(slot_start_ts: str, slot_end_ts: str) -> float:
    start = datetime.fromisoformat(slot_start_ts)
    end = datetime.fromisoformat(slot_end_ts)
    return (end - start).total_seconds() / 60


def find_feasible_slots(
    conn: sqlite3.Connection,
    shipment_id: str,
    after_ts: Optional[str] = None,
    limit: int = 5,
) -> List[SlotOption]:
    """Open, compatible, sufficiently-sized slots for this shipment.

    "Feasible" here means: open in v_slot_availability, at the right
    facility, a dock the shipment is allowed to use, long enough for the
    expected unload, and not earlier than the driver can arrive. It does
    NOT mean reserved -- see propose_booking() for that.
    """
    shipment = get_shipment(conn, shipment_id)
    if shipment is None:
        raise ValueError("Unknown shipment_id: {}".format(shipment_id))

    earliest = after_ts or shipment.effective_eta_ts
    last_new_start = _last_new_start_time(conn, shipment.destination_facility_id)

    rows = conn.execute(
        """
        SELECT * FROM v_slot_availability
        WHERE facility_id = ?
          AND availability_status = 'AVAILABLE'
          AND slot_start_ts >= ?
        ORDER BY slot_start_ts, dock_code
        """,
        (shipment.destination_facility_id, earliest),
    ).fetchall()

    options: List[SlotOption] = []
    for row in rows:
        if shipment.required_dock_type != "ANY" and row["dock_type"] != shipment.required_dock_type:
            continue
        if shipment.temperature_control_required and not row["supports_refrigerated"]:
            continue
        if row["max_vehicle_weight_kg"] < shipment.load_weight_kg:
            continue
        if _slot_duration_min(row["slot_start_ts"], row["slot_end_ts"]) < shipment.expected_unload_min:
            continue

        needs_manual_approval = False
        manual_approval_reason = None
        if last_new_start is not None:
            start_time_of_day = row["slot_start_ts"].split("T")[1][:5]
            if start_time_of_day >= last_new_start:
                needs_manual_approval = True
                manual_approval_reason = "Starts at or after the facility's last new-start time ({})".format(
                    last_new_start
                )

        options.append(
            SlotOption(
                slot_id=row["slot_id"],
                facility_id=row["facility_id"],
                dock_code=row["dock_code"],
                dock_type=row["dock_type"],
                slot_start_ts=row["slot_start_ts"],
                slot_end_ts=row["slot_end_ts"],
                needs_manual_approval=needs_manual_approval,
                manual_approval_reason=manual_approval_reason,
            )
        )
        if len(options) >= limit:
            break

    return options


def propose_booking(
    conn: sqlite3.Connection,
    shipment_id: str,
    slot_id: str,
    booking_source: str = "DRIVER_CHAT",
) -> BookingResult:
    """Attempt to hold a slot for a shipment.

    Writes PENDING_CONFIRMATION, not CONFIRMED: per the brief, showing an
    option, holding it and a warehouse confirming it are three different
    states. SQLite's partial unique indexes are the actual concurrency
    guard -- this function just surfaces the outcome cleanly instead of
    letting a driver-facing caller see a raw IntegrityError.
    """
    appointment_id = "APT-{}".format(uuid.uuid4().hex[:10].upper())
    now = _now_ist_iso()
    try:
        conn.execute(
            """
            INSERT INTO appointments (
                appointment_id, shipment_id, slot_id, appointment_status,
                booking_source, is_current, booked_at, confirmed_at,
                cancelled_at, cancellation_reason, replaced_appointment_id,
                warehouse_confirmation_ref, updated_at
            ) VALUES (?, ?, ?, 'PENDING_CONFIRMATION', ?, 1, ?, NULL, NULL, NULL, NULL, NULL, ?)
            """,
            (appointment_id, shipment_id, slot_id, booking_source, now, now),
        )
        conn.commit()
        return BookingResult(success=True, appointment_id=appointment_id, reason=None)
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        message = str(exc)
        if "appointments.slot_id" in message:
            reason = "That slot was just taken by another driver's request."
        elif "appointments.shipment_id" in message:
            reason = "This shipment already has an active appointment."
        else:
            reason = "Booking rejected: {}".format(message)
        return BookingResult(success=False, appointment_id=None, reason=reason)
