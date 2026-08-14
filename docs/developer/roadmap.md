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

## Not built yet

- Duplicate-message detection (`chat_messages.is_duplicate`, `driver_exceptions.dedupe_key` exist in the schema, unused)
- Any web/API surface (FastAPI etc.)
- The optional facility-wide scheduling-engine extension (brief §7.3) -- explicitly out of scope for the first working slice
- Live-LLM correctness testing (does the model actually classify messages right) stays manual, via `scripts/chat_demo.py` / `scripts/chat.py` -- automated tests mock the LLM call by design, see architecture.md

## Against the PDF brief

Everything in brief §7.1 (first-level challenge: one driver) and §7.2
(the real challenge: many drivers / concurrent requests) is built and
tested. §9.3 (human control: no-feasible-slot escalation, no silent
guessing) is built. Two things from the brief are explicitly **not**
done, both by design, not oversight:

- **§7.3, the optional facility-wide scheduling engine.** The brief
  itself marks this optional/advanced. Not started.
- **Duplicate-message / retry handling** (brief §11.2's "a driver sends
  duplicate messages because of weak connectivity" stress case). The
  schema has columns for it (`chat_messages.is_duplicate`,
  `driver_exceptions.dedupe_key`); nothing reads or writes them yet.

Everything else in the core brief (chat-based exception handling,
feasibility, concurrency safety, escalation, human-control boundaries) is
built. MVP 3 would reasonably be: duplicate-message handling, then the
optional scheduling engine if still wanted.

See `docs/developer/architecture.md` for how each piece works.
