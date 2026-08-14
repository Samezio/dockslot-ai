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
from typing import Dict, List, Optional

from app.models import BookingResult, Driver, ShipmentSummary, SlotOption

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


def _digits_only(s: str) -> str:
    return "".join(ch for ch in s if ch.isdigit())


def find_driver_by_phone(conn: sqlite3.Connection, phone: str) -> Optional[Driver]:
    """Look up a driver by phone number.

    This is the identity check a real channel (e.g. WhatsApp) would do
    automatically from the sender's number -- chat.py asks for it
    explicitly since there's no real channel here. Matches on the last 10
    digits so formatting differences (spaces, dashes, missing country
    code) don't cause a false miss; seed data is small enough (15 drivers)
    that scanning it in Python is fine, a real deployment would normalize
    phone numbers at write time and index/query on that instead.
    """
    suffix = _digits_only(phone)[-10:]
    if len(suffix) < 10:
        return None
    rows = conn.execute("SELECT driver_id, driver_name, phone, carrier_id, driver_status FROM drivers").fetchall()
    for row in rows:
        if _digits_only(row["phone"])[-10:] == suffix:
            return Driver(
                driver_id=row["driver_id"],
                driver_name=row["driver_name"],
                phone=row["phone"],
                carrier_id=row["carrier_id"],
                driver_status=row["driver_status"],
            )
    return None


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


# --- Conversation state persistence -----------------------------------
# Backs app/conversation.py's memory across turns using the schema's own
# chat_threads/chat_messages tables (see docs/developer/architecture.md).
# Still deterministic, still no LLM calls -- this is bookkeeping, not
# understanding.


def get_or_create_open_thread(conn: sqlite3.Connection, driver_id: str, shipment_id: str) -> str:
    """One open thread per (driver, shipment); reused across turns until
    resolved/closed."""
    row = conn.execute(
        """
        SELECT thread_id FROM chat_threads
        WHERE driver_id = ? AND shipment_id = ?
          AND thread_status NOT IN ('RESOLVED', 'CLOSED')
        ORDER BY opened_at DESC LIMIT 1
        """,
        (driver_id, shipment_id),
    ).fetchone()
    if row:
        return row["thread_id"]

    thread_id = "THR-{}".format(uuid.uuid4().hex[:8].upper())
    conn.execute(
        """
        INSERT INTO chat_threads (thread_id, driver_id, shipment_id, opened_at, closed_at, thread_status, thread_intent)
        VALUES (?, ?, ?, ?, NULL, 'OPEN', 'UNKNOWN')
        """,
        (thread_id, driver_id, shipment_id, _now_ist_iso()),
    )
    conn.commit()
    return thread_id


def set_thread_state(conn: sqlite3.Connection, thread_id: str, status: str, intent: str) -> None:
    conn.execute(
        "UPDATE chat_threads SET thread_status = ?, thread_intent = ? WHERE thread_id = ?",
        (status, intent, thread_id),
    )
    conn.commit()


def record_message(
    conn: sqlite3.Connection,
    thread_id: str,
    sender_type: str,
    message_text: str,
    parsed_intent: Optional[str] = None,
    extracted_eta_ts: Optional[str] = None,
    offered_slot_ids: Optional[List[str]] = None,
) -> str:
    message_id = "MSG-{}".format(uuid.uuid4().hex[:8].upper())
    conn.execute(
        """
        INSERT INTO chat_messages (
            chat_message_id, thread_id, sender_type, sender_reference, message_text,
            message_ts, external_message_id, is_duplicate, parsed_intent,
            extracted_eta_ts, requires_human_review, offered_slot_ids
        ) VALUES (?, ?, ?, NULL, ?, ?, NULL, 0, ?, ?, 0, ?)
        """,
        (
            message_id,
            thread_id,
            sender_type,
            message_text,
            _now_ist_iso(),
            parsed_intent,
            extracted_eta_ts,
            ",".join(offered_slot_ids) if offered_slot_ids else None,
        ),
    )
    conn.commit()
    return message_id


def get_last_offered_slot_ids(conn: sqlite3.Connection, thread_id: str) -> List[str]:
    """slot_ids from the most recent AGENT message in this thread that
    offered options, in the order they were shown. Empty if none."""
    row = conn.execute(
        """
        SELECT offered_slot_ids FROM chat_messages
        WHERE thread_id = ? AND sender_type = 'AGENT' AND offered_slot_ids IS NOT NULL
        ORDER BY message_ts DESC LIMIT 1
        """,
        (thread_id,),
    ).fetchone()
    if not row or not row["offered_slot_ids"]:
        return []
    return row["offered_slot_ids"].split(",")


def get_slots_by_ids(conn: sqlite3.Connection, slot_ids: List[str]) -> Dict[str, SlotOption]:
    """Dock/time info for specific slots regardless of current
    availability -- lets a caller resolve "the first option" against what
    was actually offered even if it's since been taken. Availability
    itself must be re-checked separately (e.g. against
    find_feasible_slots' current result) before booking."""
    if not slot_ids:
        return {}
    placeholders = ",".join("?" for _ in slot_ids)
    rows = conn.execute(
        """
        SELECT sl.slot_id, sl.facility_id, d.dock_code, d.dock_type, sl.slot_start_ts, sl.slot_end_ts
        FROM appointment_slots sl
        JOIN docks d ON d.dock_id = sl.dock_id
        WHERE sl.slot_id IN ({})
        """.format(placeholders),
        slot_ids,
    ).fetchall()
    return {
        row["slot_id"]: SlotOption(
            slot_id=row["slot_id"],
            facility_id=row["facility_id"],
            dock_code=row["dock_code"],
            dock_type=row["dock_type"],
            slot_start_ts=row["slot_start_ts"],
            slot_end_ts=row["slot_end_ts"],
            needs_manual_approval=False,
            manual_approval_reason=None,
        )
        for row in rows
    }
