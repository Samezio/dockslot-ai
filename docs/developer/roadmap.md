# Roadmap

## MVP 1 -- deterministic layer + basic chat (done)

- Schema/seed materialized from the provided data package (`db/`, `scripts/build_db.py`)
- Deterministic operational layer: shipment resolution, feasible-slot search, race-safe booking (`app/repository.py`)
- Conversational layer: intent extraction (Gemini default, provider-agnostic), orchestration (`app/llm.py`, `app/intent.py`, `app/conversation.py`)
- Interactive chat (`scripts/chat.py`)

## MVP 2 -- correctness under real use (done)

1. **Driver identity** -- phone-number lookup at chat start, no hardcoded default driver.
2. **Conversation state persistence** -- `chat_threads`/`chat_messages` wired up; "take the first option" resolves against what was actually shown, re-verified before booking, not a blind recompute.
3. **Multi-driver concurrency proof** -- `scripts/concurrency_demo.py` + `tests/test_concurrency.py`, real threads/connections, not sequential calls.
4. **`driver_exceptions` persistence** -- one record per thread, status mirrors the reply given.
5. **Automated tests** -- `tests/` (pytest, deterministic, no LLM calls; 36 tests, <1s). Covers the repository layer, `app/conversation.py`'s orchestration (LLM mocked out) and pure-function helpers, and concurrency. Also caught and fixed a real bug: "most recent row" queries ordered by timestamp alone, but timestamps are second-precision and can tie -- fixed with a `rowid` tiebreaker.

## MVP 3 -- duplicate-message detection (done)

Brief §11.2: "a driver sends duplicate messages because of weak
connectivity" (seed case THR001/THR009). `is_recent_duplicate_message`
catches an identical repeat within a still-open thread and short-circuits
before the LLM call -- acknowledged, recorded as
`chat_messages.is_duplicate = 1`, no wasted tokens/latency, thread and
exception state left untouched. Verified live (OpenRouter). Known
limitation: a retry after the thread already resolved starts a fresh
thread instead of being flagged -- see architecture.md.

## MVP 4 -- facility-wide scheduling engine (done)

Brief §7.3, the optional extension. `app/scheduling.py`: unrelated-
parallel-machines-with-eligibility scheduling (docks = machines,
shipments = jobs), solved with Google OR-Tools CP-SAT -- the tool the
brief's own references point to. Standalone module, not wired into the
per-message chat flow (see architecture.md for why). Verified against the
brief's own §7.3 worked example (two dock doors, four trucks) reproduced
as a test, and against the real seeded Jaipur facility -- solves
`OPTIMAL`, 15/16 trucks scheduled, the one left unscheduled matches what
`find_feasible_slots` independently reports for the same shipment (seed
case THR005). Surfaced and fixed a real bug along the way: two data
sources (`dock_status_events`, `facility_checkins`) can describe the same
real-world dock unavailability, and feeding both to the solver as
separate hard blocks made the whole model `INFEASIBLE`.

## Not built yet

- Any web/API surface (FastAPI etc.)
- Wiring `app/scheduling.py` into the live chat flow (e.g. a driver's
  message triggering a facility-wide recompute) -- currently a
  standalone tool, called on demand.
- Live-LLM correctness testing (does the model actually classify messages right) stays manual, via `scripts/chat_demo.py` / `scripts/chat.py` -- automated tests mock the LLM call by design, see architecture.md

## Against the PDF brief

Everything in the brief is built and tested: §7.1 (one driver), §7.2
(many drivers / concurrent requests), §7.3 (the optional facility-wide
scheduling engine), §9.3 (human control -- no-feasible-slot escalation,
no silent guessing), and §11.2 (duplicate-message handling). Nothing from
the brief's in-scope requirements remains; what's left (a web/API
surface, live-LLM test automation) is infrastructure around the brief,
not content from it.

See `docs/developer/architecture.md` for how each piece works.
