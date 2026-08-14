"""Interactive chat against the conversational layer -- type driver
messages, get real agent replies, backed by the real (seeded) database.

Run: python scripts\\build_db.py && python scripts\\chat.py [DRIVER_ID]

Calls a real LLM per message (whichever LLM_PROVIDER is set in .env), so
it needs a working API key and will incur provider cost/latency.

In-chat commands:
  /driver DRV006   switch which driver you're chatting as
  /quit            exit
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversation import handle_driver_message
from app.db import get_connection
from app.repository import find_shipments_for_driver


def _print_driver_summary(conn, driver_id):
    shipments = find_shipments_for_driver(conn, driver_id)
    if not shipments:
        print("  (no active shipment on file for {} today)".format(driver_id))
        return
    for s in shipments:
        print("  {} -- status={} eta={} destination={}".format(
            s.shipment_id, s.current_status, s.effective_eta_ts, s.destination_facility_id
        ))


def main():
    conn = get_connection()
    driver_id = sys.argv[1] if len(sys.argv) > 1 else "DRV006"

    print("dockslot-ai chat -- chatting as driver {}".format(driver_id))
    print("commands: /driver DRV0xx to switch, /quit to exit\n")
    _print_driver_summary(conn, driver_id)

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
            if line.startswith("/driver "):
                driver_id = line.split(" ", 1)[1].strip().upper()
                print("Switched to driver {}".format(driver_id))
                _print_driver_summary(conn, driver_id)
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
