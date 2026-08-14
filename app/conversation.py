"""Orchestrates one driver chat turn: LLM for understanding, deterministic
code for everything that touches capacity or a booking.

Rules this module follows (CLAUDE.md section 6, brief section 6.3):
- The LLM only extracts intent (app/intent.py). It never decides slot
  feasibility, never picks a slot, never writes to the database.
- A booking only ever happens through app.repository.propose_booking(),
  which is the one function that writes to `appointments` and relies on
  the DB's own concurrency guard.
- If we don't have enough information to act safely, we ask or escalate
  instead of guessing.

Scope note: this is a single-turn handler -- it re-derives everything
from the database on every call and does not yet persist conversation
state (chat_threads/chat_messages/driver_exceptions exist in the schema
for that; wiring them up is the natural next increment, see
docs/developer/architecture.md).
"""
from datetime import datetime
from typing import List, Optional

from app.intent import extract_intent
from app.llm_models import DriverIntent, DriverMessageIntent
from app.models import ShipmentSummary, SlotOption
from app.repository import find_feasible_slots, find_shipments_for_driver, propose_booking

# The seed data is a frozen single-day operational snapshot (see
# db/schema_and_seed.sql header). A driver's HH:MM time reference gets
# anchored to this date. A non-classroom deployment would derive "today"
# from the shipment's own planned date instead of a hardcoded constant.
OPERATIONAL_DATE = "2026-08-04"


def _to_iso(hhmm: str) -> Optional[str]:
    """'11:20' -> '2026-08-04T11:20:00+05:30', or None if not parseable."""
    try:
        datetime.strptime(hhmm, "%H:%M")
    except (ValueError, TypeError):
        return None
    return "{}T{}:00+05:30".format(OPERATIONAL_DATE, hhmm)


def _shipment_context_text(s: ShipmentSummary) -> str:
    return (
        "shipment_id={}, status={}, destination_facility={}, "
        "current_appointment_slot={} ({} - {}), "
        "latest_known_eta={} (source={})"
    ).format(
        s.shipment_id,
        s.current_status,
        s.destination_facility_id,
        s.current_slot_id,
        s.current_slot_start_ts,
        s.current_slot_end_ts,
        s.effective_eta_ts,
        s.eta_source,
    )


def _format_options(options: List[SlotOption]) -> str:
    lines = []
    for i, opt in enumerate(options, start=1):
        flag = " -- needs manual approval ({})".format(opt.manual_approval_reason) if opt.needs_manual_approval else ""
        lines.append(
            "  {}. {} {} - {}{}".format(i, opt.dock_code, opt.slot_start_ts, opt.slot_end_ts, flag)
        )
    return "\n".join(lines)


def _match_requested_option(options: List[SlotOption], reference: Optional[str]) -> Optional[SlotOption]:
    """Best-effort match of a free-text reference ("the second one", "D2",
    "7:30") against a freshly recomputed option list. Deliberately simple:
    if it can't confidently match, it returns None and the caller asks
    again rather than guessing which slot the driver meant.
    """
    if not reference or not options:
        return None
    text = reference.strip().lower()

    ordinals = {"first": 0, "1st": 0, "second": 1, "2nd": 1, "third": 2, "3rd": 2}
    for word, idx in ordinals.items():
        if word in text and idx < len(options):
            return options[idx]

    for opt in options:
        if opt.dock_code.lower() in text:
            return opt

    for opt in options:
        time_of_day = opt.slot_start_ts.split("T")[1][:5]
        if time_of_day in text:
            return opt

    return None


def handle_driver_message(conn, driver_id: str, message_text: str) -> str:
    shipments = find_shipments_for_driver(conn, driver_id)

    if not shipments:
        return "I don't see any active shipment assigned to you today. Please contact operations directly."

    if len(shipments) > 1:
        lines = "\n".join(
            "  - {} (status: {}, destination: {})".format(s.shipment_id, s.current_status, s.destination_facility_id)
            for s in shipments
        )
        return "You have more than one active shipment today. Which one is this about?\n{}".format(lines)

    shipment = shipments[0]
    intent = extract_intent(message_text, shipment_context=_shipment_context_text(shipment))

    if intent.intent in (DriverIntent.REPORT_DELAY, DriverIntent.ASK_SLOT_OPTIONS, DriverIntent.EARLY_ARRIVAL):
        return _handle_slot_request(conn, shipment, intent)

    if intent.intent == DriverIntent.CHOOSE_OPTION:
        return _handle_choose_option(conn, shipment, intent)

    if intent.intent == DriverIntent.CHECK_STATUS:
        return _handle_check_status(shipment)

    # GENERAL_QUESTION / UNKNOWN
    if intent.missing_information:
        return "Could you clarify: {}".format("; ".join(intent.missing_information))
    return (
        "I'm not able to help with that from chat. If this needs a decision beyond "
        "appointment scheduling, please contact operations directly."
    )


def _handle_slot_request(conn, shipment: ShipmentSummary, intent: DriverMessageIntent) -> str:
    if intent.declared_eta_local_time is None and intent.missing_information:
        return "Before I can check options: {}".format("; ".join(intent.missing_information))

    after_ts = _to_iso(intent.declared_eta_local_time) if intent.declared_eta_local_time else None
    effective_after = after_ts or shipment.effective_eta_ts

    still_fits = (
        shipment.current_slot_start_ts is not None
        and effective_after <= shipment.current_slot_start_ts
    )
    if still_fits:
        return "Your current appointment ({} - {}) still works based on that arrival time -- no change needed.".format(
            shipment.current_slot_start_ts, shipment.current_slot_end_ts
        )

    options = find_feasible_slots(conn, shipment.shipment_id, after_ts=after_ts, limit=3)
    if not options:
        return (
            "I can't find a feasible same-day slot for this shipment after your declared arrival time. "
            "This needs operations review -- escalating rather than guessing."
        )

    return (
        "Your original slot no longer works. Here are the next feasible options "
        "(not booked yet -- reply with which one you'd like):\n{}"
    ).format(_format_options(options))


def _handle_choose_option(conn, shipment: ShipmentSummary, intent: DriverMessageIntent) -> str:
    # Always recompute fresh -- never book against a list that might be stale.
    options = find_feasible_slots(conn, shipment.shipment_id, limit=5)
    match = _match_requested_option(options, intent.requested_slot_reference)
    if match is None:
        if not options:
            return "There are no feasible slots to choose from right now -- this needs operations review."
        return "I couldn't tell which option you meant. Current options:\n{}".format(_format_options(options))

    result = propose_booking(conn, shipment.shipment_id, match.slot_id)
    if result.success:
        return (
            "Requested {} at {} for you (appointment {}). This is pending warehouse "
            "confirmation, not confirmed yet."
        ).format(match.dock_code, match.slot_start_ts, result.appointment_id)

    remaining = find_feasible_slots(conn, shipment.shipment_id, limit=3)
    return "{} Here are the current alternatives:\n{}".format(result.reason, _format_options(remaining))


def _handle_check_status(shipment: ShipmentSummary) -> str:
    if shipment.current_appointment_id is None:
        return "You don't have an active appointment on file right now."
    return "Your appointment ({}) is for {} - {}. Status: shipment is currently {}.".format(
        shipment.current_appointment_id,
        shipment.current_slot_start_ts,
        shipment.current_slot_end_ts,
        shipment.current_status,
    )
