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
5. **Automated tests** -- `tests/` (pytest, deterministic, no LLM calls).

## Not built yet

- Tests for the conversational layer itself (needs a live LLM call; `scripts/chat_demo.py` covers this manually today)
- Duplicate-message detection (`chat_messages.is_duplicate`, `driver_exceptions.dedupe_key` exist in the schema, unused)
- Any web/API surface (FastAPI etc.)
- The optional facility-wide scheduling-engine extension (brief §7.3) -- explicitly out of scope for the first working slice

See `docs/developer/architecture.md` for how each piece works.
