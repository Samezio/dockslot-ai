"""Deterministic operational-layer tests -- promoted from scripts/demo.py's
narrated scenarios into real assertions. No LLM involved.
"""
import sqlite3

from app.repository import (
    find_driver_by_phone,
    find_feasible_slots,
    find_shipments_for_driver,
    get_shipment,
    propose_booking,
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


def test_propose_booking_rejects_second_active_appointment_for_same_shipment(conn):
    first = propose_booking(conn, "SHP1018", "SLOT-JAI-001")
    assert first.success

    second = propose_booking(conn, "SHP1018", "SLOT-JAI-002")
    assert not second.success
    assert "already has an active appointment" in second.reason


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
