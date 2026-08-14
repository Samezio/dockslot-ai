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

Conversation state (which slots were actually offered, thread status) is
persisted via app.repository's chat_threads/chat_messages helpers -- see
docs/developer/architecture.md "Conversation state persistence". A driver
picking "the first option" is resolved against what was actually shown in
this thread's last turn, re-verified for current availability, never
against a blind fresh recompute (which could silently reorder).
"""
from datetime import datetime
from typing import List, Optional, Tuple

from app.intent import extract_intent
from app.llm_models import DriverIntent, DriverMessageIntent
from app.models import ShipmentSummary, SlotOption
from app.repository import (
    find_feasible_slots,
    find_shipments_for_driver,
    get_last_offered_slot_ids,
    get_or_create_exception,
    get_or_create_open_thread,
    get_slots_by_ids,
    propose_booking,
    record_message,
    set_exception_status,
    set_thread_state,
)

# Intents that represent (or continue) an operational exception worth a
# driver_exceptions row. CHECK_STATUS/GENERAL_QUESTION/UNKNOWN don't --
# they're not reporting or acting on a delay.
_EXCEPTION_INTENTS = (
    DriverIntent.REPORT_DELAY,
    DriverIntent.ASK_SLOT_OPTIONS,
    DriverIntent.EARLY_ARRIVAL,
    DriverIntent.CHOOSE_OPTION,
)

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
    "7:30") against an ordered option list. Deliberately simple: if it
    can't confidently match, it returns None and the caller asks again
    rather than guessing which slot the driver meant.
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
        # Not persisted: no resolved shipment to attach a thread to yet,
        # and this reply is fully deterministic from `shipments` alone --
        # asking again next turn costs nothing and never goes stale.
        return "You have more than one active shipment today. Which one is this about?\n{}".format(lines)

    shipment = shipments[0]
    thread_id = get_or_create_open_thread(conn, driver_id, shipment.shipment_id)
    record_message(conn, thread_id, "DRIVER", message_text)

    intent = extract_intent(message_text, shipment_context=_shipment_context_text(shipment))

    if intent.intent in (DriverIntent.REPORT_DELAY, DriverIntent.ASK_SLOT_OPTIONS, DriverIntent.EARLY_ARRIVAL):
        reply, offered_ids, status, exc_status = _handle_slot_request(conn, shipment, intent)
    elif intent.intent == DriverIntent.CHOOSE_OPTION:
        reply, offered_ids, status, exc_status = _handle_choose_option(conn, thread_id, shipment, intent)
    elif intent.intent == DriverIntent.CHECK_STATUS:
        reply, offered_ids, status, exc_status = _handle_check_status(shipment), [], None, None
    else:
        # GENERAL_QUESTION / UNKNOWN
        if intent.missing_information:
            reply = "Could you clarify: {}".format("; ".join(intent.missing_information))
        else:
            reply = (
                "I'm not able to help with that from chat. If this needs a decision beyond "
                "appointment scheduling, please contact operations directly."
            )
        offered_ids, status, exc_status = [], None, None

    record_message(
        conn,
        thread_id,
        "AGENT",
        reply,
        parsed_intent=intent.intent.value,
        extracted_eta_ts=_to_iso(intent.declared_eta_local_time) if intent.declared_eta_local_time else None,
        offered_slot_ids=offered_ids,
    )
    if status:
        set_thread_state(conn, thread_id, status, intent.intent.value)

    if intent.intent in _EXCEPTION_INTENTS:
        exception_type = "EARLY_ARRIVAL" if intent.intent == DriverIntent.EARLY_ARRIVAL else "DELAY"
        exception_id = get_or_create_exception(
            conn,
            thread_id,
            driver_id,
            shipment.shipment_id,
            exception_type,
            description=message_text,
            declared_eta_ts=_to_iso(intent.declared_eta_local_time) if intent.declared_eta_local_time else shipment.effective_eta_ts,
            priority_code=shipment.priority_code,
        )
        if exc_status:
            set_exception_status(conn, exception_id, exc_status)

    return reply


def _handle_slot_request(
    conn, shipment: ShipmentSummary, intent: DriverMessageIntent
) -> Tuple[str, List[str], str, str]:
    if intent.declared_eta_local_time is None and intent.missing_information:
        reply = "Before I can check options: {}".format("; ".join(intent.missing_information))
        return reply, [], "WAITING_FOR_DRIVER", "NEEDS_INFORMATION"

    after_ts = _to_iso(intent.declared_eta_local_time) if intent.declared_eta_local_time else None
    effective_after = after_ts or shipment.effective_eta_ts

    still_fits = (
        shipment.current_slot_start_ts is not None
        and effective_after <= shipment.current_slot_start_ts
    )
    if still_fits:
        reply = "Your current appointment ({} - {}) still works based on that arrival time -- no change needed.".format(
            shipment.current_slot_start_ts, shipment.current_slot_end_ts
        )
        return reply, [], "RESOLVED", "RESOLVED"

    options = find_feasible_slots(conn, shipment.shipment_id, after_ts=after_ts, limit=3)
    if not options:
        reply = (
            "I can't find a feasible same-day slot for this shipment after your declared arrival time. "
            "This needs operations review -- escalating rather than guessing."
        )
        return reply, [], "ESCALATED", "ESCALATED"

    reply = (
        "Your original slot no longer works. Here are the next feasible options "
        "(not booked yet -- reply with which one you'd like):\n{}"
    ).format(_format_options(options))
    return reply, [opt.slot_id for opt in options], "WAITING_FOR_DRIVER", "SLOT_OPTIONS_SHARED"


def _handle_choose_option(
    conn, thread_id: str, shipment: ShipmentSummary, intent: DriverMessageIntent
) -> Tuple[str, List[str], str, str]:
    stored_ids = get_last_offered_slot_ids(conn, thread_id)

    if not stored_ids:
        # No record of options actually shown in this thread (e.g. the
        # driver jumped straight to "book it") -- fall back to a fresh
        # list rather than failing outright.
        options = find_feasible_slots(conn, shipment.shipment_id, limit=5)
        match = _match_requested_option(options, intent.requested_slot_reference)
        if match is None:
            if not options:
                reply = "There are no feasible slots to choose from right now -- this needs operations review."
                return reply, [], "ESCALATED", "ESCALATED"
            reply = "I couldn't tell which option you meant. Current options:\n{}".format(_format_options(options))
            return reply, [opt.slot_id for opt in options], "WAITING_FOR_DRIVER", "SLOT_OPTIONS_SHARED"
        return _book(conn, shipment, match)

    # Resolve the reference against what was ACTUALLY shown, in that
    # order -- "first option" always means position 1 of the original
    # list, even if the feasible-slot ordering has since shifted.
    slot_info = get_slots_by_ids(conn, stored_ids)
    ordered_offered = [slot_info[sid] for sid in stored_ids if sid in slot_info]
    match = _match_requested_option(ordered_offered, intent.requested_slot_reference)
    if match is None:
        reply = "I couldn't tell which option you meant. Options I showed you:\n{}".format(_format_options(ordered_offered))
        return reply, stored_ids, "WAITING_FOR_DRIVER", "SLOT_OPTIONS_SHARED"

    still_available = {opt.slot_id for opt in find_feasible_slots(conn, shipment.shipment_id, limit=10)}
    if match.slot_id not in still_available:
        remaining = find_feasible_slots(conn, shipment.shipment_id, limit=3)
        reply = "That option ({} at {}) is no longer available. Current options:\n{}".format(
            match.dock_code, match.slot_start_ts, _format_options(remaining)
        )
        return reply, [opt.slot_id for opt in remaining], "WAITING_FOR_DRIVER", "SLOT_OPTIONS_SHARED"

    return _book(conn, shipment, match)


def _book(conn, shipment: ShipmentSummary, match: SlotOption) -> Tuple[str, List[str], str, str]:
    result = propose_booking(conn, shipment.shipment_id, match.slot_id)
    if result.success:
        reply = (
            "Requested {} at {} for you (appointment {}). This is pending warehouse "
            "confirmation, not confirmed yet."
        ).format(match.dock_code, match.slot_start_ts, result.appointment_id)
        return reply, [], "WAITING_FOR_WAREHOUSE", "WAITING_CONFIRMATION"

    remaining = find_feasible_slots(conn, shipment.shipment_id, limit=3)
    reply = "{} Here are the current alternatives:\n{}".format(result.reason, _format_options(remaining))
    return reply, [opt.slot_id for opt in remaining], "WAITING_FOR_DRIVER", "SLOT_OPTIONS_SHARED"


def _handle_check_status(shipment: ShipmentSummary) -> str:
    if shipment.current_appointment_id is None:
        return "You don't have an active appointment on file right now."
    return "Your appointment ({}) is for {} - {}. Status: shipment is currently {}.".format(
        shipment.current_appointment_id,
        shipment.current_slot_start_ts,
        shipment.current_slot_end_ts,
        shipment.current_status,
    )
