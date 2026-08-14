# Architecture

## The problem (from `.project_details/SetuHaul_FDE_Challenge (1).pdf`)

SetuHaul is a logistics company. Drivers report delays/exceptions via chat.
A coordinator has to: identify the shipment, check whether the current dock
appointment still works, find compatible alternative slots, and book one --
all while several drivers may be competing for the same limited dock
capacity at once.

The brief is explicit that this splits into two layers that must stay
separate:

- **Conversational layer** (LLM): understands free-text messages, asks only
  for missing information, presents options, tracks conversation state.
- **Operational layer** (deterministic code): feasibility, capacity,
  concurrency-safe booking, confirmation status. The LLM must never decide
  whether two drivers can have the same slot, whether a booking actually
  committed, or how scarce capacity is prioritised.

This mirrors CLAUDE.md's business-logic-vs-AI-orchestration principle
directly, so the codebase is structured around that same boundary.

## Data

`db/schema_and_seed.sql` is the source of truth: a SQLite schema + seed
data provided with the challenge (originally under `.project_details/`,
which is gitignored -- this file is the tracked, load-bearing copy of it).
`scripts/build_db.py` rebuilds `data/dockslot.db` (gitignored, generated)
from it. Never hand-edit `data/dockslot.db`; edit the seed script and
rebuild.

Key tables: `drivers`, `vehicles`, `shipments`, `eta_updates`,
`facility_checkins`, `facilities`, `docks`, `appointment_slots`,
`appointments`, `facility_rules`, `chat_threads`/`chat_messages`,
`driver_exceptions`. Four views do most of the cross-table integration
work: `v_latest_eta`, `v_slot_availability`, `v_inbound_operational_state`,
`v_current_facility_queue`. See `.project_details/extracted/inner/
setuhaul_database_guide.md` for the full table/edge-case reference (that
guide is gitignored along with the rest of `.project_details/`, so treat
this architecture doc as the durable summary of it).

### Concurrency guarantee lives in the database, not in Python

```sql
CREATE UNIQUE INDEX ux_active_appointment_per_slot
ON appointments(slot_id)
WHERE appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS');

CREATE UNIQUE INDEX ux_current_active_appointment_per_shipment
ON appointments(shipment_id)
WHERE is_current = 1
  AND appointment_status IN ('PENDING_CONFIRMATION','CONFIRMED','IN_PROGRESS');
```

Two simultaneous booking attempts for the same slot: the second `INSERT`
fails with `sqlite3.IntegrityError`. `app/repository.py::propose_booking()`
catches that and returns a clean `BookingResult(success=False, reason=...)`
instead of either crashing or trying to reimplement locking in app code.
This is the answer to the brief's "how do you prevent double-booking under
concurrency" question for the current single-process, single-DB scope --
it would need revisiting (e.g. a real lock/queue) if this ever became a
multi-writer distributed setup, but there is no evidence that's needed yet.

## Code layers

```
app/db.py           connection helper (sqlite3, foreign_keys=ON, row access by name)
app/models.py        plain dataclasses returned by the repository
app/repository.py    deterministic business logic -- the "operational layer"
```

`app/repository.py` has no LLM calls and no free-text parsing. It is called
the same way by a human, a test, or (later) an agent's tool-calling layer:

- `find_shipments_for_driver(conn, driver_id)` -- can return >1 shipment
  (a driver may have two loads the same day); callers must ask the driver
  to disambiguate rather than guessing.
- `find_feasible_slots(conn, shipment_id, after_ts=None, limit=5)` --
  filters by facility, dock-type compatibility, refrigeration, vehicle
  weight, unload duration, and the driver's earliest possible arrival.
  Flags (doesn't reject) slots that would need manual approval under a
  facility rule (e.g. `LAST_NEW_START_TIME`).
- `propose_booking(conn, shipment_id, slot_id, booking_source)` -- writes
  `PENDING_CONFIRMATION` (not `CONFIRMED`) and surfaces the DB's
  concurrency guard as a typed result.

Dataclasses, not Pydantic, for `app/models.py`: this layer only moves
structured DB rows around, so there's nothing to validate beyond what the
schema's own `CHECK`/`FOREIGN KEY` constraints already enforce. Pydantic
is the right tool at the boundary where LLM output gets parsed (CLAUDE.md
section 9) -- that's `app/llm_models.py`, described below.

## Conversational layer

```
app/llm.py           provider-agnostic chat-model factory (langchain)
app/llm_models.py     Pydantic schema for the one LLM call's output
app/intent.py         the LLM call: message text -> DriverMessageIntent
app/conversation.py   orchestration: intent -> app/repository.py calls -> reply text
scripts/chat.py        interactive chat loop -- the actual way to use this by hand
scripts/chat_demo.py   non-interactive walkthrough of the same layer against seeded cases
```

One LLM call per driver message (`app/intent.py::extract_intent`), forced
into the `DriverMessageIntent` schema via `.with_structured_output(...)`.
That's the entire LLM surface. `app/conversation.py::handle_driver_message`
takes the validated intent and does everything else deterministically:

- Never resolves an ambiguous driver -> shipment match itself; if
  `find_shipments_for_driver` returns more than one active shipment, it
  always asks, even if the message plausibly refers to one of them.
- Computes slot feasibility and re-derives the option list itself
  (`app/repository.py::find_feasible_slots`) -- the LLM's job is only to
  extract *what the driver said* (a declared time, a delay, which
  previously-offered option they picked), never to decide *what's
  available*.
- Only calls `propose_booking()` when the driver's message is classified
  `CHOOSE_OPTION` and can be matched (by ordinal, dock code, or time) against
  a **freshly recomputed** option list -- never against a list shown in an
  earlier turn, which could be stale.
- Every reply is built from template strings around real query results,
  not LLM-phrased -- lower latency, one fewer place for the model to
  invent something. Revisit if replies need to sound less mechanical once
  this is validated with real users.

### Provider abstraction (`app/llm.py`)

Swapping providers is a config change, not a code change:

```
LLM_PROVIDER=google_genai   # default; also supports: openai, openrouter
```

`get_chat_model()` reads `LLM_PROVIDER` (or an explicit argument) and
returns a langchain `BaseChatModel` via `init_chat_model()`. Every
provider is called through that same interface (`.invoke()`,
`.with_structured_output()`), so nothing outside `app/llm.py` needs to
know which one is active. Adding a new provider = one entry in
`app/llm.py`'s `_PROVIDERS` dict + installing its langchain integration
package (e.g. `langchain-openai`); `openrouter` is already wired as an
OpenAI-wire-compatible entry (see the dict for how).

Currently active: **Gemini** (`google_genai`, reading `GOOGLE_API_KEY`
from `.env`). `openrouter` has also been verified working end-to-end; its
entry in `app/llm.py` caps `max_tokens=300` as a dev-safety measure since
that key has limited credits -- raise/remove once it's not a shared
low-credit dev key. See `.env.example` for the full set of variables
`app/llm.py` recognizes.

## Not built yet

- Conversation state persistence (`chat_threads`/`chat_messages`/
  `driver_exceptions` exist in the schema for this). Right now
  `handle_driver_message` is stateless -- every call re-derives everything
  from the DB, so a driver picking "the second option" only works within
  the options just recomputed in that same call, not across a real
  multi-turn back-and-forth. Persisting messages/threads is the natural
  next increment once this is validated.
- Any web/API surface (FastAPI etc.) -- deferred until there's a reason to
  serve this over HTTP (e.g. a real chat channel) rather than call it
  in-process.
- The optional facility-wide scheduling-engine extension from the brief
  (§7.3) -- explicitly out of scope for the first working slice.
- Automated tests (`tests/`) -- `scripts/demo.py` (deterministic layer) and
  `scripts/chat_demo.py` (conversational layer, hits a real LLM) currently
  play that role as narrated walkthroughs; promote their scenarios into
  real tests once the shape of both layers stabilizes.
