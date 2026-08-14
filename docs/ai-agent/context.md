# Context for AI coding agents

Read `docs/developer/architecture.md` first -- this file only adds what's
specific to working on this project as an agent.

## Current state (as of this writing)

Built: the deterministic operational layer only (`app/db.py`,
`app/models.py`, `app/repository.py`) plus DB tooling
(`db/schema_and_seed.sql`, `scripts/build_db.py`, `scripts/demo.py`).

Not built: the conversational/LLM layer, any web/API surface, automated
tests, the optional scheduling-engine extension.

## Open decision: LLM provider

CLAUDE.md's default AI stack lists both OpenRouter and Google Gemini.
Neither has been chosen yet for this project -- ask the developer before
wiring one in (CLAUDE.md section 2: ask before introducing a new external
service).

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
