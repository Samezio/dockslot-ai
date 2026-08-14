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
  `CHOOSE_OPTION` and can be matched (by ordinal, dock code, or time)
  against the options **actually shown in this thread's last turn**
  (persisted -- see below), re-verified for current availability right
  before booking. Never against a blind fresh recompute, which could
  silently reorder and make "the first one" mean something different than
  what the driver saw.
- Every reply is built from template strings around real query results,
  not LLM-phrased -- lower latency, one fewer place for the model to
  invent something. Revisit if replies need to sound less mechanical once
  this is validated with real users.

### Conversation state persistence

Backed by the schema's own `chat_threads`/`chat_messages` tables (one
open thread per driver+shipment, reused across turns via
`app/repository.py::get_or_create_open_thread`). Every turn is recorded
(`record_message`) with the classified intent and, when options were
offered, their `slot_id`s in order (`offered_slot_ids` -- a column we
added to `chat_messages`; not part of the original provided schema). A
later "take the first option" resolves via `get_last_offered_slot_ids` +
`get_slots_by_ids` against that exact stored list/order, not a fresh
`find_feasible_slots` call -- availability is still re-checked against a
fresh call before booking, so staleness fails safely (a clear "that
option's gone, here are current alternatives" reply) rather than
silently.

Ambiguous-driver replies (multiple active shipments) are deliberately
**not** persisted -- there's no resolved shipment to attach a thread to,
and the reply is fully deterministic from `find_shipments_for_driver`
alone, so re-asking costs nothing and can't go stale.

`driver_exceptions` is now wired up too: one exception record per thread
(`get_or_create_exception`, reused across turns like the thread itself),
created for REPORT_DELAY/ASK_SLOT_OPTIONS/EARLY_ARRIVAL/CHOOSE_OPTION (not
for CHECK_STATUS or general questions -- those aren't reporting or acting
on a delay). `exception_type`/`description`/`severity_code` (mapped from
the shipment's `priority_code`) are set once at first report;
`exception_status` moves each turn via `set_exception_status`, mirrored
1:1 with the reply actually given (`SLOT_OPTIONS_SHARED`,
`WAITING_CONFIRMATION`, `RESOLVED`, `ESCALATED`, `NEEDS_INFORMATION`).

### Duplicate-message detection

Brief §11.2: "a driver sends duplicate messages because of weak
connectivity" (seed case THR001/THR009 -- a retry spawned a whole second
thread there). Our thread-per-(driver,shipment) reuse already prevents
that specific failure mode; the remaining case is the same message
arriving twice within one still-open thread.
`app/repository.py::is_recent_duplicate_message` checks the thread's last
DRIVER message: same text (normalized for case/whitespace) within 5
minutes = duplicate. `app/conversation.py` checks this **before** calling
`extract_intent` -- a detected duplicate is recorded
(`chat_messages.is_duplicate = 1`) and acknowledged with a canned reply,
skipping the LLM call entirely (cost/latency win, and nothing new was
actually said) and leaving thread/exception status untouched.

Known limitation: dedup only applies within an open thread. If the first
reply already resolved the thread (`get_or_create_open_thread` only
reuses threads not in `RESOLVED`/`CLOSED`), an identical retry starts a
fresh thread rather than being flagged -- observed live: asking the same
already-answered "still fits, no change needed" question twice created
two separate (harmless, but undeduplicated) threads. Worth revisiting if
it turns out to matter in practice; not fixed now since duplicating a
resolved answer is low-cost, unlike duplicating an open, LLM-processed
one.

### Provider abstraction (`app/llm.py`)

Swapping providers is a config change, not a code change:

```
LLM_PROVIDER=google_genai   # default; also: openai, openrouter, aicredit, ollama
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

## Multi-driver concurrency proof

`scripts/concurrency_demo.py` -- separate threads, separate DB
connections, synchronized with a `threading.Barrier` so they call
`propose_booking()`/`find_feasible_slots()` at the same instant, not
sequentially. Two scenarios: N threads forced onto one exact slot (the
mechanical proof), and 5 threads each independently asking "what's my
best slot after 18:00?" -- the brief's own §7.2 example ("five drivers
may ask for a 6:00 PM window when only one compatible dock is free").
Outcomes vary run to run (it's a real race), but every genuine collision
resolves to exactly one winner, verified by querying `appointments`
directly afterward -- not by trusting the Python-level return values.
No LLM involved.

## Facility-wide scheduling engine (`app/scheduling.py`, brief §7.3 -- optional)

Everything above answers one driver's own question (`find_feasible_slots`
only needs that shipment's view). This is the separate, optional tool the
brief describes: look at ALL trucks relevant to one facility together
(already-in-a-dock, waiting in the yard, still in transit) and propose a
whole-day dock assignment. It's a standalone module, not wired into
`app/conversation.py`'s per-message flow -- called on demand
(`scripts/scheduling_demo.py`), never interprets free text, same
tool-boundary discipline as the rest of `app/repository.py`.

**Modeling.** This is the classic *unrelated parallel machines with
eligibility* scheduling problem: docks = machines, shipments = jobs, a
job may only run on docks it's physically compatible with. Solved with
[Google OR-Tools CP-SAT](https://github.com/google/or-tools/blob/stable/ortools/sat/docs/scheduling.md)
-- the tool the brief's own references point to. One optional interval
variable per (job, eligible-dock) pair sharing a common start/end
variable; `add_no_overlap` per dock across everything assigned to it
(including fixed/blocked intervals); and, critically, each job gets
*"exactly one of {assigned to some eligible dock, explicitly left
unscheduled}"* rather than a hard "must be assigned" constraint -- an
overloaded day degrades to "some trucks don't get a slot today" (reported
explicitly) instead of the whole model going infeasible.

**Data boundary** (brief §7.3, verbatim): only the original planned ETA,
the latest driver-declared ETA, and actual gate-in time once a truck
reaches the facility. No live GPS, nothing beyond what's already in the
database -- `build_facility_snapshot` reads `v_inbound_operational_state`,
`facility_checkins`, `dock_status_events`, `docks`, `facility_rules`, and
(for a soft deadline) `appointments`/`driver_exceptions.latest_acceptable_ts`.

**Objective**: minimize `sum(priority_weight * waiting_time)` +
`10 * sum(priority_weight * tardiness)` for jobs with a known deadline
(their current booked slot's end, or a driver-stated latest-acceptable
time) + `10,000 * sum(priority_weight * left_unscheduled)`. The large
gaps between tiers are deliberate, not tuned business policy: always
prefer scheduling over not, always prefer meeting a stated deadline over
merely minimizing generic waiting. `priority_code` maps to an ordinal
weight (CRITICAL=4 ... LOW=1) per the brief's "shipment priority: a
weight or penalty" framing.

**A real bug this surfaced**: `dock_status_events` and
`facility_checkins` can describe the *same* real-world unavailability
from two different angles -- the seed data has a `CAPACITY_REDUCTION`
event on `DOCK-JAI-D2` (08:00-09:20) that exists precisely *because*
`SHP1002` overran in that same dock (in-progress 08:05-09:15). Feeding
both to the solver as separate mandatory blocking intervals is a hard,
unsatisfiable conflict (confirmed live: the whole model came back
`INFEASIBLE`) regardless of any job. Fixed by merging overlapping fixed
occupancies per dock (`_merge_overlapping_occupancies`) before they reach
the solver. Worth remembering if another data source describing dock
unavailability gets added later.

**Verified**: the brief's own §7.3 worked example (SHP-201..204, two
dock doors) reproduced directly as a test, plus the real Jaipur facility
snapshot solves to `OPTIMAL` with 15/16 trucks scheduled -- the one left
unscheduled (`SHP1015`) is *independently* the same shipment
`find_feasible_slots` already reports as having no feasible slot (seed
case THR005), a good cross-check that both layers agree. See
`tests/test_scheduling.py` and `scripts/scheduling_demo.py`.

## REST API + UI (`app/api.py`, `web/index.html`)

A thin HTTP wrapper over everything above -- FastAPI, per CLAUDE.md
section 13's default for REST APIs. It adds no business logic: every
route calls straight into `app/repository.py`, `app/conversation.py`, or
`app/scheduling.py`, the same functions `scripts/chat.py` and
`scripts/scheduling_demo.py` already used. Request/response shapes are
explicit Pydantic models in `app/api_schemas.py`, kept separate from the
routes the same way `app/llm_models.py` is kept separate from
`app/intent.py`.

Endpoints:

- `POST /identify` -- phone number -> driver + their active shipments.
  Same identity check as `scripts/chat.py` (no hardcoded default driver).
- `POST /chat` -- `{driver_id, message}` -> one turn of
  `handle_driver_message`. `driver_id` must come from a prior `/identify`
  call; the route only checks it's a real driver_id (`app/repository.py::
  get_driver`), it doesn't re-verify phone ownership.
- `GET /schedule/{facility_id}` -- runs `app/scheduling.py` on demand and
  returns the proposed whole-day dock assignment. Read-only; doesn't
  write anything. **Deliberately not auto-triggered from `/chat`** -- a
  driver's message still only affects that one shipment
  (`find_feasible_slots`), exactly as before. Wiring scheduling into the
  live chat flow (e.g. every driver message triggering a facility-wide
  recompute) would be a materially different, heavier behavior and stays
  a real design decision for later, not something this endpoint does
  implicitly.
- `GET /` serves `web/index.html`, a single self-contained static page
  (vanilla JS, no build step, no framework) that drives all three
  endpoints: identify -> chat transcript -> optional facility-schedule
  view.

**No authentication.** This is a local/dev tool like the rest of the
project so far -- `driver_id` isn't cryptographically tied to the phone
number that produced it, and there's no session/token layer. Fine for
local use; would need real auth before being reachable from an untrusted
network.

**DB access**: one `sqlite3` connection per request
(`app/api.py::get_db`, a FastAPI dependency) rather than a shared
connection -- simplest option for a single-process dev tool, and
consistent with the existing principle that the DB's own partial unique
indexes are the concurrency guard, not anything in application code.

Run: `python scripts/serve.py`, then open `http://127.0.0.1:8000`. See
`docs/developer/development.md` for details.
`tests/test_api.py` covers the HTTP wiring (status codes, dependency
override to a temp-file test DB, LLM mocked out same as
`test_conversation.py`) -- not business logic, already covered elsewhere.

## Automated tests

`tests/` -- pytest, deterministic only (no LLM calls, no network). Each
test gets a fresh `:memory:` DB from `db/schema_and_seed.sql`
(`tests/conftest.py`), except `tests/test_concurrency.py` which needs a
real temp *file* DB to test genuine multi-connection/multi-thread access
(`:memory:` doesn't share across connections without special URI
handling). `scripts/demo.py`, `scripts/concurrency_demo.py`, and
`scripts/chat_demo.py` remain as narrated walkthroughs -- useful for
watching what happens, not a substitute for `pytest`. Run: `pytest -q`
(needs `requirements-dev.txt`).

`tests/test_conversation.py` covers `app/conversation.py`'s orchestration
by monkeypatching `app.conversation.extract_intent` to return a canned
`DriverMessageIntent` -- everything downstream (persistence, option
matching, escalation, booking) runs for real, no LLM call needed. Includes
the direct regression test for the ordinal-drift bug the persistence
layer fixed (`test_choose_option_resolves_against_persisted_list_not_
fresh_recompute`): books nothing rather than silently booking whatever a
fresh recompute would now put first.

Writing tests here surfaced a real bug: `get_last_offered_slot_ids` (and
the other "most recent row" queries) ordered by timestamp alone, but
`record_message`'s timestamps are second-precision -- two turns in the
same second tie, and ties aren't guaranteed to break in insertion order.
Fixed with an explicit `rowid DESC` tiebreaker on every such query in
`app/repository.py`.

`tests/test_scheduling.py` covers `app/scheduling.py`: the brief's own
§7.3 worked example as a golden test, priority/deadline-driven contention
resolution, graceful degradation under overload, and the real seeded
Jaipur facility (including the merged-fixed-occupancy fix above).

## Not built yet

- Authentication on the REST API -- see "REST API + UI" above.
- `app/scheduling.py` is still not auto-triggered from
  `app/conversation.py`'s per-message flow -- it's called on demand, now
  via `GET /schedule/{facility_id}` as well as
  `scripts/scheduling_demo.py`. Auto-triggering it from a driver's
  message is a real design decision -- see the module docstring for why
  it's deliberately kept separate for now.
- Live-LLM correctness testing (does the model actually classify
  messages right) stays manual, via `scripts/chat_demo.py` /
  `scripts/chat.py` -- automated tests mock the LLM call by design (see
  "Automated tests" above).
