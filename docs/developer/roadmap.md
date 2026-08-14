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

## Not built yet

- Any web/API surface (FastAPI etc.)
- The optional facility-wide scheduling-engine extension (brief §7.3) -- explicitly out of scope for the first working slice
- Live-LLM correctness testing (does the model actually classify messages right) stays manual, via `scripts/chat_demo.py` / `scripts/chat.py` -- automated tests mock the LLM call by design, see architecture.md

## Against the PDF brief

Everything in brief §7.1 (first-level challenge: one driver), §7.2 (the
real challenge: many drivers / concurrent requests), and §11.2's
duplicate-message stress case is built and tested. §9.3 (human control:
no-feasible-slot escalation, no silent guessing) is built. One thing from
the brief is explicitly **not** done, by design, not oversight:

- **§7.3, the optional facility-wide scheduling engine.** The brief
  itself marks this optional/advanced. Not started.

Everything else in the core brief (chat-based exception handling,
feasibility, concurrency safety, escalation, duplicate handling,
human-control boundaries) is built. The scheduling engine is the only
sizeable piece of the brief left, and it's optional.

See `docs/developer/architecture.md` for how each piece works.
