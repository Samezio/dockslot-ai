"""app/conversation.py orchestration tests -- deterministic, no LLM calls.

app.conversation.extract_intent is monkeypatched to return a canned
DriverMessageIntent per test, so these exercise everything AROUND the LLM
call (persistence, option matching, escalation, booking) without needing
a live API key. Live-LLM behavior (does the model actually classify a
message correctly) is covered manually by scripts/chat_demo.py -- see
docs/developer/architecture.md.

Driver/shipment choices below are deliberate, not arbitrary -- the seed
data is densely interlinked (most shipments already have a confirmed
appointment, most "chatty" drivers already have a seeded thread), so each
test uses whichever driver actually has the property it needs:
  - DRV001/SHP1017: has an existing CONFIRMED appointment (for
    status-check and still-fits scenarios) and a pre-existing seeded
    thread (fine when a test doesn't assert exact message counts).
  - DRV010/SHP1010: single active shipment, NO seeded thread at all,
    55-minute unload (fits a 60-minute slot) -- used where a test asserts
    an exact, clean message history.
  - DRV012/SHP1012: single active shipment, NO current appointment --
    the only kind that can accept a brand new booking in these tests.
  - DRV015/SHP1015: seed case THR005, the reefer-dock-unavailable
    escalation.
"""
from app.conversation import handle_driver_message
from app.llm_models import DriverIntent, DriverMessageIntent
from app.repository import find_feasible_slots, get_last_offered_slot_ids, propose_booking


def _intent(intent, **overrides):
    fields = dict(
        mentioned_shipment_reference=None,
        reported_delay_minutes=None,
        declared_eta_local_time=None,
        latest_acceptable_local_time=None,
        requested_slot_reference=None,
        missing_information=[],
        confidence="HIGH",
    )
    fields.update(overrides)
    return DriverMessageIntent(intent=intent, **fields)


def _mock_extract(monkeypatch, response):
    monkeypatch.setattr("app.conversation.extract_intent", lambda message_text, shipment_context=None: response)


def _refuse_to_be_called(*_args, **_kwargs):
    raise AssertionError("extract_intent should not have been called")


def test_ambiguous_driver_asks_without_calling_llm(conn, monkeypatch):
    monkeypatch.setattr("app.conversation.extract_intent", _refuse_to_be_called)
    reply = handle_driver_message(conn, "DRV004", "I will be late by 45 minutes.")
    assert "more than one active shipment" in reply
    assert "SHP1004" in reply and "SHP1020" in reply


def test_no_active_shipment_replies_without_calling_llm(conn, monkeypatch):
    # DRV008's only shipments today (SHP1008, SHP1019) are both CANCELLED.
    monkeypatch.setattr("app.conversation.extract_intent", _refuse_to_be_called)
    reply = handle_driver_message(conn, "DRV008", "Is my 4 PM slot still active?")
    assert "don't see any active shipment" in reply


def test_report_delay_offers_options_and_persists_thread_and_exception(conn, monkeypatch):
    _mock_extract(monkeypatch, _intent(DriverIntent.ASK_SLOT_OPTIONS, declared_eta_local_time="12:45"))
    reply = handle_driver_message(conn, "DRV010", "What slots are possible after 1 PM if I reach around 12:45?")
    assert "feasible options" in reply

    thread = conn.execute(
        "SELECT thread_id, thread_status, thread_intent FROM chat_threads WHERE driver_id='DRV010' AND shipment_id='SHP1010'"
    ).fetchone()
    assert thread is not None
    assert thread["thread_status"] == "WAITING_FOR_DRIVER"
    assert thread["thread_intent"] == "ASK_SLOT_OPTIONS"

    messages = conn.execute(
        "SELECT sender_type, offered_slot_ids FROM chat_messages WHERE thread_id = ? ORDER BY message_ts", (thread["thread_id"],)
    ).fetchall()
    assert [m["sender_type"] for m in messages] == ["DRIVER", "AGENT"]
    assert messages[1]["offered_slot_ids"], "agent message should record what it offered"

    exception = conn.execute(
        "SELECT exception_type, exception_status, declared_eta_ts FROM driver_exceptions WHERE thread_id = ?", (thread["thread_id"],)
    ).fetchone()
    assert exception["exception_type"] == "DELAY"
    assert exception["exception_status"] == "SLOT_OPTIONS_SHARED"
    assert exception["declared_eta_ts"] == "2026-08-04T12:45:00+05:30"


def test_declared_eta_still_fits_current_slot_resolves(conn, monkeypatch):
    # SHP1017 already has a confirmed appointment; "06:00" is before every
    # slot in the seed data (facility opens no earlier than 06:00, first
    # slots start 08:00), so the original appointment must still stand.
    _mock_extract(monkeypatch, _intent(DriverIntent.REPORT_DELAY, declared_eta_local_time="06:00"))
    reply = handle_driver_message(conn, "DRV001", "Reaching super early actually.")
    assert "still works" in reply.lower()

    exception = conn.execute(
        "SELECT exception_status FROM driver_exceptions WHERE driver_id='DRV001' AND shipment_id='SHP1017'"
    ).fetchone()
    assert exception["exception_status"] == "RESOLVED"


def test_missing_eta_asks_for_clarification_instead_of_guessing(conn, monkeypatch):
    _mock_extract(monkeypatch, _intent(DriverIntent.REPORT_DELAY, missing_information=["how late will you be?"]))
    reply = handle_driver_message(conn, "DRV001", "I'm late.")
    assert "how late will you be?" in reply

    thread = conn.execute("SELECT thread_status FROM chat_threads WHERE driver_id='DRV001' AND shipment_id='SHP1017'").fetchone()
    assert thread["thread_status"] == "WAITING_FOR_DRIVER"
    exception = conn.execute(
        "SELECT exception_status FROM driver_exceptions WHERE driver_id='DRV001' AND shipment_id='SHP1017'"
    ).fetchone()
    assert exception["exception_status"] == "NEEDS_INFORMATION"


def test_no_feasible_slot_escalates_instead_of_inventing_one(conn, monkeypatch):
    # seed case THR005: SHP1015's only compatible (reefer) dock is under
    # maintenance by 18:30.
    _mock_extract(monkeypatch, _intent(DriverIntent.REPORT_DELAY, declared_eta_local_time="18:30"))
    reply = handle_driver_message(conn, "DRV015", "Evening traffic. ETA 6:30.")
    assert "escalat" in reply.lower()

    exception = conn.execute("SELECT exception_status FROM driver_exceptions WHERE driver_id='DRV015'").fetchone()
    assert exception["exception_status"] == "ESCALATED"


def test_choose_option_books_the_offered_slot(conn, monkeypatch):
    # SHP1012 has no current appointment -- required for a NEW booking to
    # succeed (ux_current_active_appointment_per_shipment would reject it
    # for a shipment that already has one, e.g. SHP1017).
    _mock_extract(monkeypatch, _intent(DriverIntent.ASK_SLOT_OPTIONS, declared_eta_local_time="12:45"))
    handle_driver_message(conn, "DRV012", "Any slots open after 12:45?")

    _mock_extract(monkeypatch, _intent(DriverIntent.CHOOSE_OPTION, requested_slot_reference="the second one"))
    reply = handle_driver_message(conn, "DRV012", "Take the second option please.")
    assert "pending warehouse confirmation" in reply

    booked = conn.execute(
        "SELECT slot_id FROM appointments WHERE shipment_id='SHP1012' AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')"
    ).fetchone()
    assert booked is not None

    thread = conn.execute("SELECT thread_status FROM chat_threads WHERE driver_id='DRV012' AND shipment_id='SHP1012'").fetchone()
    assert thread["thread_status"] == "WAITING_FOR_WAREHOUSE"
    exception = conn.execute(
        "SELECT exception_status FROM driver_exceptions WHERE driver_id='DRV012' AND shipment_id='SHP1012'"
    ).fetchone()
    assert exception["exception_status"] == "WAITING_CONFIRMATION"


def test_choose_option_resolves_against_persisted_list_not_fresh_recompute(conn, monkeypatch):
    """The actual regression test for the ordinal-drift bug the persistence
    layer fixed: "first option" must mean position 1 of what was ACTUALLY
    shown, even if a fresh recompute would now put something else first."""
    _mock_extract(monkeypatch, _intent(DriverIntent.ASK_SLOT_OPTIONS, declared_eta_local_time="12:45"))
    handle_driver_message(conn, "DRV012", "Any slots open after 12:45?")

    thread_id = conn.execute(
        "SELECT thread_id FROM chat_threads WHERE driver_id='DRV012' AND shipment_id='SHP1012'"
    ).fetchone()["thread_id"]
    offered = get_last_offered_slot_ids(conn, thread_id)
    assert len(offered) >= 2
    original_first = offered[0]

    # Another driver grabs the originally-first slot in between turns.
    steal = propose_booking(conn, "SHP1018", original_first)
    assert steal.success

    # Confirm this test actually exercises the fix: a fresh recompute now
    # puts something ELSE first.
    fresh = find_feasible_slots(conn, "SHP1012", after_ts="2026-08-04T12:45:00+05:30", limit=5)
    assert fresh and fresh[0].slot_id != original_first

    _mock_extract(monkeypatch, _intent(DriverIntent.CHOOSE_OPTION, requested_slot_reference="the first option"))
    reply = handle_driver_message(conn, "DRV012", "Take the first option please.")

    assert "no longer available" in reply.lower()
    booked = conn.execute(
        "SELECT slot_id FROM appointments WHERE shipment_id='SHP1012' AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS')"
    ).fetchone()
    assert booked is None, "must not silently book whatever is now first in a fresh list"


def test_check_status_reports_current_appointment(conn, monkeypatch):
    _mock_extract(monkeypatch, _intent(DriverIntent.CHECK_STATUS))
    reply = handle_driver_message(conn, "DRV001", "Has the warehouse confirmed my slot?")
    assert "APT1017" in reply  # SHP1017's seeded confirmed appointment


def test_general_question_with_missing_info_asks_clarification(conn, monkeypatch):
    _mock_extract(monkeypatch, _intent(DriverIntent.GENERAL_QUESTION, missing_information=["which facility do you mean?"]))
    reply = handle_driver_message(conn, "DRV001", "What about the other place?")
    assert "which facility do you mean?" in reply
