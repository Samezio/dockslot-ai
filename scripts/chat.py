"""Interactive chat against the conversational layer.

Run: python scripts\\build_db.py && python scripts\\chat.py

Identifies the driver by phone number first (like a real channel, e.g.
WhatsApp, would from the sender's number) -- there is no default driver.
Calls a real LLM per message (whichever LLM_PROVIDER is set in .env).

In-chat commands:
  /switch   re-identify as a different driver (asks for phone again)
  /quit     exit

Dev shortcut (bypasses identification, prints a warning that it did):
  python scripts\\chat.py --driver DRV006
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversation import handle_driver_message
from app.db import get_connection
from app.repository import find_driver_by_phone, find_shipments_for_driver


def _print_shipment_summary(conn, driver_id):
    shipments = find_shipments_for_driver(conn, driver_id)
    if not shipments:
        print("  (no active shipment on file for {} today)".format(driver_id))
        return
    for s in shipments:
        print("  {} -- status={} eta={} destination={}".format(
            s.shipment_id, s.current_status, s.effective_eta_ts, s.destination_facility_id
        ))


def identify_driver(conn):
    """Ask for a phone number and look it up. Returns a driver_id, or None
    if the user quit instead of identifying."""
    while True:
        phone = input("Enter your phone number: ").strip()
        if phone in ("/quit", "/exit"):
            return None
        if not phone:
            continue
        driver = find_driver_by_phone(conn, phone)
        if driver is None:
            print("No driver found with that number. Try again, or /quit to exit.")
            continue
        if driver.driver_status != "ACTIVE":
            print("This account is {}. Please contact operations directly.".format(driver.driver_status))
            continue
        print("Welcome, {} ({}).".format(driver.driver_name, driver.driver_id))
        _print_shipment_summary(conn, driver.driver_id)
        return driver.driver_id


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--driver", help="Dev shortcut: skip phone identification, chat as this driver_id")
    args = parser.parse_args()

    conn = get_connection()

    if args.driver:
        driver_id = args.driver.upper()
        print("[dev shortcut] identification bypassed -- chatting as {}".format(driver_id))
    else:
        driver_id = identify_driver(conn)
        if driver_id is None:
            conn.close()
            return

    print("\ncommands: /switch to re-identify, /quit to exit")

    try:
        while True:
            try:
                line = input("\n{}> ".format(driver_id)).strip()
            except EOFError:
                break
            if not line:
                continue
            if line in ("/quit", "/exit"):
                break
            if line == "/switch":
                new_id = identify_driver(conn)
                if new_id:
                    driver_id = new_id
                continue

            reply = handle_driver_message(conn, driver_id, line)
            print("Agent: {}".format(reply))
    except KeyboardInterrupt:
        pass
    finally:
        conn.close()
        print("\nbye")


if __name__ == "__main__":
    main()
