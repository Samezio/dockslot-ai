"""Deterministic operational-layer tests -- promoted from scripts/demo.py's
narrated scenarios into real assertions. No LLM involved.
"""
import sqlite3

from app.repository import (
    find_driver_by_phone,
    find_feasible_slots,
    find_shipments_for_driver,
    get_current_appointment_id,
    get_last_offered_slot_ids,
    get_or_create_open_thread,
    get_shipment,
    is_recent_duplicate_message,
    is_slot_available_for,
    propose_booking,
    record_message,
)


def test_ambiguous_driver_returns_all_active_shipments(conn):
    # seed case THR010: DRV004 has two active shipments today
    shipments = find_shipments_for_driver(conn, "DRV004")
    assert {s.shipment_id for s in shipments} == {"SHP1004", "SHP1020"}


def test_completed_and_cancelled_shipments_excluded(conn):
    # DRV001 also has SHP1001 (COMPLETED) -- must not show up as "active"
    shipments = find_shipments_for_driver(conn, "DRV001")
    assert [s.shipment_id for s in shipments] == ["SHP1017"]


def test_original_slot_infeasible_once_eta_passes_slot_start(conn):
    # seed case THR001: SHP1006 booked 10:00-11:00, driver's latest declared
    # ETA is 11:20 -- the original slot must read as no-longer-feasible.
    shipment = get_shipment(conn, "SHP1006")
    assert shipment.current_slot_start_ts == "2026-08-04T10:00:00+05:30"
    assert shipment.effective_eta_ts == "2026-08-04T11:20:00+05:30"
    assert shipment.effective_eta_ts > shipment.current_slot_start_ts


def test_feasible_slots_respect_declared_eta_and_are_ordered(conn):
    options = find_feasible_slots(conn, "SHP1006", after_ts="2026-08-04T11:20:00+05:30", limit=5)
    assert options, "expected at least one feasible alternative"
    for opt in options:
        assert opt.slot_start_ts >= "2026-08-04T11:20:00+05:30"
    starts = [opt.slot_start_ts for opt in options]
    assert starts == sorted(starts)


def test_reefer_shipment_only_gets_reefer_docks(conn):
    options = find_feasible_slots(conn, "SHP1015", limit=10)
    for opt in options:
        assert opt.dock_type == "REEFER"


def test_reefer_dock_under_maintenance_yields_no_feasible_slot(conn):
    # seed case THR005: driver's new ETA (18:30) is after the only reefer
    # dock's maintenance window starts -- must escalate, not invent a slot.
    options = find_feasible_slots(conn, "SHP1015", after_ts="2026-08-04T18:30:00+05:30", limit=5)
    assert options == []


def test_heavy_shipment_only_gets_heavy_docks(conn):
    options = find_feasible_slots(conn, "SHP1016", after_ts="2026-08-04T12:00:00+05:30", limit=10)
    assert options
    for opt in options:
        assert opt.dock_type == "HEAVY"


def test_propose_booking_succeeds_for_open_slot(conn):
    result = propose_booking(conn, "SHP1018", "SLOT-JAI-001")
    assert result.success
    assert result.appointment_id is not None


def test_propose_booking_rejects_conflicting_slot(conn):
    first = propose_booking(conn, "SHP1018", "SLOT-JAI-001")
    assert first.success

    second = propose_booking(conn, "SHP1021", "SLOT-JAI-001")
    assert not second.success
    assert "taken" in second.reason.lower()

    # Prove it from the actual stored rows, not just the return value.
    count = conn.execute(
        """
        SELECT COUNT(*) c FROM appointments
        WHERE slot_id = 'SLOT-JAI-001'
          AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
        """
    ).fetchone()["c"]
    assert count == 1


def test_second_booking_for_same_shipment_reschedules_rather_than_rejecting(conn):
    """This test used to assert the opposite -- that a second booking was
    rejected. That was codifying a bug: it made rescheduling impossible
    for any shipment that already had an appointment, which is the brief's
    central workflow. A second booking is a MOVE, not a duplicate."""
    first = propose_booking(conn, "SHP1018", "SLOT-JAI-001")
    assert first.success
    assert first.replaced_appointment_id is None

    options = find_feasible_slots(conn, "SHP1018", limit=5)
    target = next(o.slot_id for o in options if o.slot_id != "SLOT-JAI-001")

    second = propose_booking(conn, "SHP1018", target)
    assert second.success, second.reason
    assert second.replaced_appointment_id == first.appointment_id

    # Still exactly one active appointment -- rescheduling must never
    # leave a shipment holding two.
    active = conn.execute(
        """
        SELECT COUNT(*) c FROM appointments
        WHERE shipment_id = 'SHP1018' AND is_current = 1
          AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
        """
    ).fetchone()["c"]
    assert active == 1


def test_find_driver_by_phone_tolerates_formatting(conn):
    # drivers.phone for DRV006 is '+91-9000010006'
    driver = find_driver_by_phone(conn, "9000010006")
    assert driver is not None
    assert driver.driver_id == "DRV006"

    driver_with_dashes = find_driver_by_phone(conn, "+91-9000010006")
    assert driver_with_dashes.driver_id == "DRV006"


def test_find_driver_by_phone_unknown_number_returns_none(conn):
    assert find_driver_by_phone(conn, "0000000000") is None


def test_find_driver_by_phone_too_short_returns_none(conn):
    assert find_driver_by_phone(conn, "12345") is None


def test_get_last_offered_slot_ids_picks_truly_latest_on_timestamp_tie(conn):
    # record_message's timestamps have only second precision, so two
    # messages written in the same test (same wall-clock second) can
    # collide -- the "most recent" query must still pick the actually
    # latest row (via a rowid tiebreaker), not an arbitrary tied one.
    thread_id = get_or_create_open_thread(conn, "DRV010", "SHP1010")
    record_message(conn, thread_id, "AGENT", "older options", offered_slot_ids=["SLOT-A", "SLOT-B"])
    record_message(conn, thread_id, "AGENT", "newer options", offered_slot_ids=["SLOT-C", "SLOT-D"])

    assert get_last_offered_slot_ids(conn, thread_id) == ["SLOT-C", "SLOT-D"]


def test_is_recent_duplicate_message_detects_exact_repeat_ignoring_case_and_whitespace(conn):
    thread_id = get_or_create_open_thread(conn, "DRV010", "SHP1010")
    record_message(conn, thread_id, "DRIVER", "Traffic delay, ETA 12:45")

    assert is_recent_duplicate_message(conn, thread_id, "Traffic delay, ETA 12:45") is True
    assert is_recent_duplicate_message(conn, thread_id, "  TRAFFIC delay,   ETA 12:45  ") is True
    assert is_recent_duplicate_message(conn, thread_id, "Different message entirely") is False


def test_is_recent_duplicate_message_false_with_no_prior_driver_message(conn):
    thread_id = get_or_create_open_thread(conn, "DRV010", "SHP1010")
    assert is_recent_duplicate_message(conn, thread_id, "hello") is False


# --- Rescheduling ------------------------------------------------------
# A delayed driver moving their appointment is the brief's core workflow.
# It was silently impossible for 78% of active shipments until
# propose_booking learned to replace an existing appointment rather than
# only ever insert a new one.


def test_reschedule_replaces_existing_appointment_and_frees_the_old_slot(conn):
    # SHP1017 has a CONFIRMED appointment (APT1017 on SLOT-JAI-005).
    original = get_current_appointment_id(conn, "SHP1017")
    assert original == "APT1017"

    options = find_feasible_slots(conn, "SHP1017", after_ts="2026-08-04T15:00:00+05:30", limit=1)
    assert options, "need a later feasible slot for this test to mean anything"

    result = propose_booking(conn, "SHP1017", options[0].slot_id)
    assert result.success
    assert result.replaced_appointment_id == original

    old = conn.execute(
        "SELECT appointment_status, is_current FROM appointments WHERE appointment_id = ?", (original,)
    ).fetchone()
    assert old["appointment_status"] == "CANCELLED"
    assert old["is_current"] == 0

    new = conn.execute(
        "SELECT slot_id, is_current, replaced_appointment_id FROM appointments WHERE appointment_id = ?",
        (result.appointment_id,),
    ).fetchone()
    assert new["slot_id"] == options[0].slot_id
    assert new["is_current"] == 1
    assert new["replaced_appointment_id"] == original, "the replacement must be auditable"

    # Exactly one active appointment -- the invariant the unique index exists to protect.
    active = conn.execute(
        """
        SELECT COUNT(*) c FROM appointments
        WHERE shipment_id = 'SHP1017' AND is_current = 1
          AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')
        """
    ).fetchone()["c"]
    assert active == 1

    # The slot they gave up must go back into circulation for other drivers.
    freed = conn.execute(
        "SELECT availability_status FROM v_slot_availability WHERE slot_id = 'SLOT-JAI-005'"
    ).fetchone()
    assert freed["availability_status"] == "AVAILABLE"


def test_failed_reschedule_leaves_the_original_appointment_intact(conn):
    """The reason we claim the new slot BEFORE releasing the old one.

    If the new slot is lost to another driver mid-conversation, the driver
    must still hold the appointment they started with -- never be left
    with nothing, which is worse than not having asked.
    """
    contested = "SLOT-JAI-008"
    assert propose_booking(conn, "SHP1012", contested).success  # another driver gets there first

    before = get_current_appointment_id(conn, "SHP1017")
    result = propose_booking(conn, "SHP1017", contested)

    assert result.success is False
    assert "just taken" in result.reason
    assert get_current_appointment_id(conn, "SHP1017") == before

    still_theirs = conn.execute(
        "SELECT appointment_status, is_current FROM appointments WHERE appointment_id = ?", (before,)
    ).fetchone()
    assert still_theirs["appointment_status"] == "CONFIRMED"
    assert still_theirs["is_current"] == 1


def test_first_booking_for_a_shipment_with_no_appointment_is_not_a_reschedule(conn):
    # SHP1012 has no current appointment -- nothing to replace.
    assert get_current_appointment_id(conn, "SHP1012") is None
    options = find_feasible_slots(conn, "SHP1012", limit=1)
    result = propose_booking(conn, "SHP1012", options[0].slot_id)

    assert result.success
    assert result.replaced_appointment_id is None


def test_is_slot_available_for_does_not_depend_on_window_or_limit(conn):
    """Regression: the availability re-check used to be
    `slot_id in find_feasible_slots(...)`, which is windowed and capped.

    A slot late in the day is available, but a windowed+capped search
    starting from the shipment's stored ETA never reaches it -- so the
    driver was told a perfectly bookable slot was "no longer available"
    and the reschedule could never complete.
    """
    late = find_feasible_slots(conn, "SHP1017", after_ts="2026-08-04T16:00:00+05:30", limit=1)
    assert late, "need a late-in-day feasible slot for this test"
    late_slot_id = late[0].slot_id

    # The capped, differently-windowed search does NOT contain it...
    windowed = {o.slot_id for o in find_feasible_slots(conn, "SHP1017", limit=10)}
    assert late_slot_id not in windowed, "test is meaningless if the windowed search finds it anyway"

    # ...but the slot is genuinely available, and the direct check says so.
    assert is_slot_available_for(conn, "SHP1017", late_slot_id) is True


def test_is_slot_available_for_rejects_taken_and_incompatible_slots(conn):
    taken = "SLOT-JAI-002"  # APT1003 / SHP1003, CONFIRMED
    assert is_slot_available_for(conn, "SHP1017", taken) is False

    # SHP1015 is a reefer load; a dry dock slot must never pass.
    dry = conn.execute(
        """
        SELECT slot_id FROM v_slot_availability
        WHERE availability_status = 'AVAILABLE' AND dock_type != 'REEFER'
          AND facility_id = (SELECT destination_facility_id FROM shipments WHERE shipment_id='SHP1015')
        LIMIT 1
        """
    ).fetchone()
    if dry:
        assert is_slot_available_for(conn, "SHP1015", dry["slot_id"]) is False

    assert is_slot_available_for(conn, "SHP1017", "SLOT-DOES-NOT-EXIST") is False
