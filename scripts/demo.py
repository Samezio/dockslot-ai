"""Exercise app/repository.py against real seeded edge cases.

Not a test suite (see tests/ for that later) -- this is a readable,
narrated walkthrough of the deterministic layer so it's obvious what it
does before any conversational/LLM layer sits on top of it.

Run: python scripts/build_db.py && python scripts/demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import get_connection
from app.repository import find_feasible_slots, find_shipments_for_driver, propose_booking


def section(title):
    print("\n=== {} ===".format(title))


def main():
    conn = get_connection()

    section("1. Ambiguous driver (DRV004 has two active shipments -- seed case THR010)")
    shipments = find_shipments_for_driver(conn, "DRV004")
    for s in shipments:
        print("  {}  status={}  eta={}".format(s.shipment_id, s.current_status, s.effective_eta_ts))
    if len(shipments) > 1:
        print("  -> must ask the driver which shipment before doing anything else")

    section("2. Normal resolve + feasibility (DRV006 / SHP1006 -- seed case THR001)")
    shipments = find_shipments_for_driver(conn, "DRV006")
    shp1006 = next(s for s in shipments if s.shipment_id == "SHP1006")
    print("  current appointment slot: {} ({} - {})".format(
        shp1006.current_slot_id, shp1006.current_slot_start_ts, shp1006.current_slot_end_ts
    ))
    print("  effective ETA: {} (source={}, confidence={})".format(
        shp1006.effective_eta_ts, shp1006.eta_source, shp1006.eta_confidence
    ))
    still_fits = (
        shp1006.current_slot_start_ts is not None
        and shp1006.effective_eta_ts <= shp1006.current_slot_start_ts
    )
    print("  original slot still feasible given latest ETA? {}".format(still_fits))

    options = find_feasible_slots(conn, "SHP1006", limit=3)
    print("  feasible alternatives after declared ETA:")
    for opt in options:
        flag = " (needs manual approval: {})".format(opt.manual_approval_reason) if opt.needs_manual_approval else ""
        print("    {} {} {}-{}{}".format(opt.slot_id, opt.dock_code, opt.slot_start_ts, opt.slot_end_ts, flag))

    section("3. Reefer compatibility + no feasible slot (SHP1015 -- seed case THR005)")
    reefer_options = find_feasible_slots(conn, "SHP1015", limit=3)
    if not reefer_options:
        print("  no feasible same-day reefer slot found -> this must escalate, not invent an answer")
    else:
        for opt in reefer_options:
            print("    {} {} {}-{}".format(opt.slot_id, opt.dock_code, opt.slot_start_ts, opt.slot_end_ts))

    section("4. Booking + race condition (two drivers want the same open slot)")
    # SHP1018 and SHP1021 currently have no active appointment (see build_db sanity check).
    target_slot = "SLOT-JAI-001"
    first = propose_booking(conn, "SHP1018", target_slot)
    print("  driver A (SHP1018) requests {}: success={} appointment_id={}".format(
        target_slot, first.success, first.appointment_id
    ))
    second = propose_booking(conn, "SHP1021", target_slot)
    print("  driver B (SHP1021) requests the SAME slot moments later: success={} reason={}".format(
        second.success, second.reason
    ))

    conn.close()


if __name__ == "__main__":
    main()
