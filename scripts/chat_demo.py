"""Live walkthrough of the conversational layer -- this one calls a real
LLM (unlike scripts/demo.py, which is fully offline/deterministic) so it
needs a working API key in .env for whichever LLM_PROVIDER is selected.

Run: python scripts\\build_db.py && python scripts\\chat_demo.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.conversation import handle_driver_message
from app.db import get_connection


SCENARIOS = [
    # (driver_id, message, what it's testing)
    ("DRV001", "What slots are possible after 1 PM if I reach around 12:45?",
     "seed case THR011 -- single shipment, feasible alternatives offered"),
    ("DRV004", "I will be late by 45 minutes.",
     "seed case THR010 -- driver has two shipments, must disambiguate before anything else"),
    ("DRV015", "Evening traffic. ETA 6:30. Can the reefer unload tonight?",
     "seed case THR005 -- only compatible dock unavailable -> escalate, don't invent a slot"),
    ("DRV008", "Is my 4 PM slot still active?",
     "seed case THR012 -- driver's only shipment today is cancelled"),
]

# A two-turn exchange in the same conversation: ask for options, then pick
# one by ordinal reference. Demonstrates the write path (propose_booking)
# actually running behind a live chat turn, not just the read paths above.
FOLLOW_UP = [
    ("DRV012", "Tyre repaired, I can reach by 11:10. Any slots open after that?"),
    ("DRV012", "Take the first option please."),
]


def main():
    conn = get_connection()
    for driver_id, message, note in SCENARIOS:
        print("\n=== {} ({}) ===".format(driver_id, note))
        print("Driver: {}".format(message))
        reply = handle_driver_message(conn, driver_id, message)
        print("Agent:  {}".format(reply.replace("\n", "\n        ")))

    print("\n=== DRV012 (two-turn: ask options, then choose one -- exercises propose_booking) ===")
    for driver_id, message in FOLLOW_UP:
        print("Driver: {}".format(message))
        reply = handle_driver_message(conn, driver_id, message)
        print("Agent:  {}".format(reply.replace("\n", "\n        ")))

    conn.close()


if __name__ == "__main__":
    main()
