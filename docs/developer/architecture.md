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
section 9) -- that's the conversational layer, not built yet.

## Not built yet

- Conversational/LLM layer (parses driver chat, asks clarifying questions,
  calls `app/repository.py` functions as controlled tools). Provider
  choice (OpenRouter vs Gemini) is still open -- see
  `docs/ai-agent/context.md`.
- Any web/API surface (FastAPI etc.) -- deferred until there's a
  conversational layer worth serving.
- The optional facility-wide scheduling-engine extension from the brief
  (§7.3) -- explicitly out of scope for the first working slice.
- Automated tests (`tests/`) -- `scripts/demo.py` currently plays that role
  as a narrated walkthrough; promote its scenarios into real tests once the
  shape of the conversational layer stabilizes.
