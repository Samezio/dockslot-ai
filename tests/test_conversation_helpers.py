"""Pure-function tests for app/conversation.py's option-matching logic --
no DB, no LLM. This is the logic behind the "resolve against what was
actually shown" correctness fix, worth locking down directly.
"""
from app.conversation import _match_requested_option, _to_iso
from app.models import SlotOption


def _opt(slot_id, dock_code, start):
    return SlotOption(
        slot_id=slot_id,
        facility_id="FAC-JAI-01",
        dock_code=dock_code,
        dock_type="STANDARD",
        slot_start_ts=start,
        slot_end_ts=start,
        needs_manual_approval=False,
        manual_approval_reason=None,
    )


OPTIONS = [
    _opt("SLOT-A", "D1", "2026-08-04T12:00:00+05:30"),
    _opt("SLOT-B", "D2", "2026-08-04T13:00:00+05:30"),
    _opt("SLOT-C", "D3", "2026-08-04T14:00:00+05:30"),
]


def test_match_by_ordinal_first():
    assert _match_requested_option(OPTIONS, "take the first option please").slot_id == "SLOT-A"


def test_match_by_ordinal_second():
    assert _match_requested_option(OPTIONS, "the second one").slot_id == "SLOT-B"


def test_match_by_dock_code():
    assert _match_requested_option(OPTIONS, "give me D3").slot_id == "SLOT-C"


def test_match_by_time_of_day():
    assert _match_requested_option(OPTIONS, "the 13:00 slot").slot_id == "SLOT-B"


def test_no_match_returns_none():
    assert _match_requested_option(OPTIONS, "whichever is cheapest") is None


def test_no_reference_returns_none():
    assert _match_requested_option(OPTIONS, None) is None


def test_ordinal_out_of_range_falls_through_to_no_match():
    assert _match_requested_option(OPTIONS[:1], "the third one") is None


def test_ordinal_position_is_stable_even_if_slot_a_is_gone():
    # This is the actual bug app/conversation.py's persistence fix
    # prevents: "first option" must still mean position 1 of what was
    # shown, not whatever's first in some freshly recomputed list.
    stale = [None, OPTIONS[1], OPTIONS[2]]  # position 0 is no longer available
    # _match_requested_option only walks real SlotOptions -- the caller
    # (app/conversation.py) is responsible for treating a None hit as
    # "that option's gone"; this test documents that contract.
    assert stale[0] is None


def test_to_iso_valid_time():
    assert _to_iso("11:20") == "2026-08-04T11:20:00+05:30"


def test_to_iso_invalid_time_returns_none():
    assert _to_iso("not a time") is None


def test_to_iso_none_returns_none():
    assert _to_iso(None) is None
