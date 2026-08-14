# Context for AI coding agents

Read `docs/developer/architecture.md` first -- this file only adds what's
specific to working on this project as an agent.

## Current state (as of this writing)

Built: the deterministic operational layer (`app/db.py`, `app/models.py`,
`app/repository.py`) plus DB tooling (`db/schema_and_seed.sql`,
`scripts/build_db.py`, `scripts/demo.py`); a conversational layer
(`app/llm.py`, `app/llm_models.py`, `app/intent.py`, `app/conversation.py`,
`scripts/chat.py`, `scripts/chat_demo.py`) with driver identity by phone
lookup (no hardcoded default) and conversation state persisted via
`chat_threads`/`chat_messages` (see architecture.md).

`driver_exceptions` is wired up too (one record per thread, status mirrors
the reply). `scripts/concurrency_demo.py` + `tests/test_concurrency.py`
prove the DB guard under real concurrent access (threads + separate
connections + a `threading.Barrier`), not just sequential calls.
Duplicate-message detection (`is_recent_duplicate_message`) catches an
exact repeat within a still-open thread and skips the LLM call entirely.
`tests/` (pytest, deterministic, no LLM, 40 tests) covers the repository
layer, `app/conversation.py`'s orchestration (LLM mocked via
monkeypatching `app.conversation.extract_intent`) and pure-function
helpers, and concurrency.

The optional facility-wide scheduling engine (brief §7.3) is built too:
`app/scheduling.py`, solved with Google OR-Tools CP-SAT, standalone --
not auto-triggered from the live chat flow. `tests/test_scheduling.py`
covers it, including the brief's own §7.3 worked example reproduced
directly as a test. Everything in the brief is now built; see
`docs/developer/roadmap.md`'s "Against the PDF brief" section.

A REST API + UI is built too: `app/api.py` (FastAPI), `app/api_schemas.py`
(request/response Pydantic models), `web/index.html` (a single
self-contained static page, vanilla JS). `POST /identify`, `POST /chat`,
`GET /schedule/{facility_id}` -- the last one exposes the scheduling
engine on demand, still never auto-triggered from `/chat`. No
authentication -- local/dev tool. `tests/test_api.py` covers it. Run:
`python scripts/serve.py`.

Not built: authentication on the REST API, auto-triggering the
scheduling engine from the live chat flow. Full status:
`docs/developer/roadmap.md`.

## LLM provider

Chosen: **Gemini** (`google_genai`), by explicit developer decision, with
the requirement that switching providers stay a config change. `app/llm.py`
implements that via langchain's `init_chat_model` -- see
`docs/developer/architecture.md`'s "Provider abstraction" section.

`openrouter` has also been verified working live (it needs
`langchain-openai` installed -- it rides the "openai" langchain provider
with a different `base_url`). Its `_PROVIDERS` entry caps `max_tokens=300`
because that key has limited credits and is dev-only for now -- don't
raise/remove that cap without checking with the developer first, and don't
add similar caps to other providers unless asked; it's specific to that
key's situation, not a general policy.

`OPEN_ROUNTER_API_KEY` (note the typo -- that's the actual name already in
the developer's `.env` and in `app/llm.py`, don't silently "fix" it without
checking first). `OPENAI_API_KEY` is present in `.env` but that key has no
credits yet, per the developer -- don't assume `openai` works without
checking.

## Conventions established so far

- **Business logic vs AI orchestration stays separated** (CLAUDE.md
  section 6). `app/repository.py` must never import an LLM client or parse
  free text. When the conversational layer is built, it should call
  `app/repository.py` functions as explicit tools -- it should not
  reimplement feasibility/booking logic inline in a prompt or agent node.
- **The DB enforces concurrency safety, not application code.** Don't add
  application-level locking/mutexes for slot booking -- the partial unique
  indexes in `db/schema_and_seed.sql` already do this (see architecture.md
  for details). If a booking write fails, catch `sqlite3.IntegrityError`
  and turn it into a `BookingResult`, the way `propose_booking()` does.
- **`data/dockslot.db` is generated, never hand-edited.** It's gitignored.
  Change `db/schema_and_seed.sql` and rerun `scripts/build_db.py`.
- **Dataclasses for structured DB data, Pydantic reserved for LLM-output
  boundaries.** Don't add Pydantic to `app/models.py` just for
  consistency -- add it where the conversational layer parses model
  output.
- **Python 3.9.6 compatibility** (CLAUDE.md section 4): no `match`
  statements, no `X | Y` union syntax in annotations, no `zoneinfo`
  IANA-name lookups without a bundled `tzdata` (the dev machine's Python
  didn't have it -- `app/repository.py` uses a fixed `timezone(timedelta(...))`
  offset instead, which also matches the `+05:30`-style timestamps already
  used throughout the seed data).
- **The LLM only extracts intent -- it never picks a slot or books one.**
  A driver's chosen option is matched against what was actually persisted
  as shown in that thread's last turn (`get_last_offered_slot_ids` +
  `get_slots_by_ids`), re-verified for current availability right before
  booking -- never a slot reference invented by the model, and never a
  blind fresh recompute (which could silently reorder).
- **Replies are template strings around real query results, not
  LLM-phrased**, to keep the LLM surface to exactly one call per message
  and avoid a second place for it to invent something. If this needs to
  read less mechanical later, that's a deliberate second LLM call to add,
  not a reason to let the first call free-form the response.
- **Conversation state persists via `chat_threads`/`chat_messages`**
  (`app/repository.py`'s `get_or_create_open_thread`/`record_message`/
  `get_last_offered_slot_ids`/`get_slots_by_ids`/`set_thread_state`). One
  open thread per (driver, shipment), reused across turns -- including
  reusing a pre-seeded thread if one's already open for that pair (seen in
  practice: DRV012's seeded THR002 got reused, not duplicated). Ambiguous-
  driver replies (no resolved shipment yet) are deliberately NOT
  persisted -- see architecture.md for why.
- **"Most recent row" queries break ties with `rowid DESC`, not just a
  timestamp column.** `record_message`'s timestamps are second-precision,
  so two turns in the same second tie. If you add another "get the latest
  X" query, give it the same tiebreaker or it can silently pick the wrong
  row (this was a real bug, found via `tests/test_repository.py::
  test_get_last_offered_slot_ids_picks_truly_latest_on_timestamp_tie`).
- **Duplicate detection is checked before calling the LLM, not after.**
  `is_recent_duplicate_message` runs on the raw message text, in
  `handle_driver_message`, before `extract_intent` -- a detected duplicate
  short-circuits entirely (no LLM call, no exception/thread status
  change). Only applies within a thread that's still open; a retry after
  the thread resolved starts a fresh thread instead (known limitation,
  see architecture.md).
- **Seed data is densely interlinked -- pick test fixtures deliberately,
  not arbitrarily.** Most shipments already have a confirmed appointment
  (can't accept a new booking); most "chatty" drivers already have a
  seeded thread (breaks tests that assert an exact/clean message count).
  `tests/test_conversation.py`'s module docstring lists which
  driver/shipment has which property; check it (or query fresh) before
  picking a new one.
- **`chat_messages.offered_slot_ids` is a column we added**, not part of
  the original provided schema (comma-separated slot_ids, order
  preserved). Existing seed INSERTs were patched to append `,NULL`. If you
  add more columns to seeded tables, patch every existing positional
  INSERT the same way -- SQLite's column-less `INSERT INTO t VALUES(...)`
  requires a value for every column.
- **`chat_threads.thread_intent`'s CHECK constraint was extended** to add
  `CHOOSE_OPTION` -- the original schema's list of 6 didn't match
  `app/llm_models.py`'s `DriverIntent` enum of 7. If that enum gains
  another value, the CHECK constraint needs the same update or inserts
  using it will fail with `IntegrityError` (this happened once already,
  live, via `set_thread_state`).
- **`driver_exceptions` is one record per thread**, reused across turns
  like the thread itself (`get_or_create_exception`). `exception_type`/
  `description`/`severity_code` are set once at first report and don't
  change on follow-ups; only `exception_status` moves each turn
  (`set_exception_status`), mirrored 1:1 with the reply actually given.
  Only created for REPORT_DELAY/ASK_SLOT_OPTIONS/EARLY_ARRIVAL/
  CHOOSE_OPTION -- not CHECK_STATUS or general questions.
- **Automated tests are deterministic-only, no LLM calls** (`tests/`,
  pytest). Each test gets its own fresh DB built from
  `db/schema_and_seed.sql` -- `:memory:` normally, a temp file DB in
  `tests/test_concurrency.py` (needed for genuine multi-connection
  access). Don't add a test that calls a real LLM provider -- that's what
  `scripts/chat_demo.py` is for, run manually.
- **`app/scheduling.py` never gets called from `app/conversation.py`.**
  It's a standalone, on-demand tool (facility-wide), not part of the
  per-driver chat path (which stays on `find_feasible_slots`, a single
  shipment's view). `app/api.py` exposes it as its own endpoint (`GET
  /schedule/{facility_id}`) -- that's still on-demand, not auto-triggered.
  Don't add a call from `handle_driver_message`/`POST /chat` into the
  scheduling engine without treating that as the real architectural
  decision it would be (see the module docstring for the reasoning) --
  check with the developer first.
- **`app/api.py` has no authentication.** `driver_id` passed to `POST
  /chat` isn't cryptographically tied to the phone number `/identify`
  returned it for -- fine for a local/dev tool, not for an untrusted
  network. Don't add a persistence/session layer for this without
  checking first (CLAUDE.md section 14/section 9 on new architecture).
- **When two data sources can describe the same real-world state, check
  for double-counting before treating both as independent hard
  constraints.** `app/scheduling.py` hit this for real:
  `dock_status_events` and `facility_checkins` both described the same
  dock overrun, and feeding both to the solver as separate mandatory
  blocks made the whole model `INFEASIBLE`
  (`_merge_overlapping_occupancies` fixes it). If you add a new fixed/
  blocking-interval source to the scheduler, check whether it can overlap
  an existing one for the same physical resource.
- **`app/scheduling.py`'s objective tiers are deliberately far apart**
  (waiting < 10x tardiness < 10,000x unscheduled) -- not tuned business
  policy, just enough separation that the solver's priority ordering
  (schedule > meet a deadline > minimize generic waiting) never inverts
  by accident. Don't "simplify" these to closer values without checking
  the tests still enforce the intended ordering
  (`tests/test_scheduling.py`'s priority/deadline tests).
- **`.project_details/` is gitignored** (it holds the original challenge
  PDF and a nested zip of the data package). Nothing in the tracked repo
  should depend on it existing -- `db/schema_and_seed.sql` is the durable,
  tracked copy of the schema/seed data that came from it.

## Seeded edge cases worth knowing about

The seed data (21 shipments, 12 chat threads) was deliberately built with
specific scenarios in mind -- reefer-dock incompatibility, dock breakdown,
no-show grace periods, an ambiguous two-shipment driver, a duplicate
message retry, priority conflicts, etc. `scripts/demo.py` exercises a few
of these already. Before inventing synthetic test data for new work,
check whether a seeded case already covers it -- the full list is in
`.project_details/extracted/inner/setuhaul_database_guide.md` section 6
(gitignored, but present locally if `.project_details/` hasn't been
deleted).
