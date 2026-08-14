# Known issues

Findings from a deep review of the codebase (2026-08-14), after the REST
API + UI landed. One file per confirmed bug; the lower-severity gaps are
grouped in `shortcomings.md`.

**Every item here was reproduced by running it**, not inferred from
reading. Each file records the exact command/output that proves it, so a
fix can be verified against the same evidence.

Nothing here is fixed yet. The whole test suite (57 tests) passes with
all of these present -- see "Why the tests don't catch these" below.

## Bugs

| # | Issue | Severity | Impact |
|---|---|---|---|
| [01](01-multi-shipment-driver-deadlock.md) | Multi-shipment drivers can never use the chat | **High** | 4 of 15 drivers permanently stuck, including the one advertised in the UI |
| [02](02-scheduler-moves-confirmed-appointments.md) | Scheduler silently reassigns CONFIRMED appointments | **High** | 12 of 15 proposals move existing bookings, unflagged |
| [03](03-scheduler-output-not-bookable.md) | Scheduler output can't be booked | **High** | 12 of 15 proposals land on times with no slot to book |
| [04](04-manual-approval-boundary-mismatch.md) | Last-new-start rule never enforced by the scheduler | **Medium** | The facility's after-hours rule is silently inert on that path |
| [05](05-option-matching-substring.md) | Option matching is substring-based | **Medium** (latent) | Can book the wrong dock silently |

## Shortcomings

See [shortcomings.md](shortcomings.md) -- 8 lower-severity gaps
(unhandled LLM failure, inconsistent driver-status checks, ignored
open-ended dock outages, `REOPENED` events treated backwards, string
timestamp comparisons, and others).

## Checked and cleared

Recorded so nobody re-investigates:

- **sqlite across FastAPI threadpool threads.** The suspicion was that
  `app/api.py::get_db` (a sync dependency) and the sync route could run
  on different threadpool threads, tripping sqlite's `check_same_thread`
  guard -- the classic trap that forces `check_same_thread=False`
  elsewhere. Tested creation, use, *and* generator cleanup: same thread
  every time. Not a bug, no change needed.
- **`_merge_overlapping_occupancies`** looks at a flat `merged[-1]`
  while iterating per-dock, but its `merged[-1].dock_id == dock_id`
  guard makes that correct.
- **CP-SAT empty variable domain.** The `release + duration > day_end`
  check in `build_facility_snapshot` does correctly prevent
  `new_int_var(lo, hi)` being called with `lo > hi`.

## Why the tests don't catch these

Not bad luck -- structural blind spots:

- `tests/test_conversation.py` only ever drives **single-shipment**
  drivers (DRV001/DRV010/DRV012/DRV015), by deliberate fixture choice.
  Issue 01 lives entirely in the multi-shipment branch.
- `tests/test_scheduling.py`'s synthetic snapshots all pass
  `last_new_start_minutes=None`, so issue 04's comparison never runs.
  The one test that does set it (`test_late_start_after_last_new_start_
  flagged_for_manual_approval`, `last_new_start_minutes=100`) uses a
  start well past the boundary, so `>` and `>=` agree there.
- The scheduling tests assert solver invariants (no overlap, eligibility,
  release times) but never cross-check a proposal against
  `appointment_slots` or against existing `appointments` -- which is
  exactly what issues 02 and 03 are about.
