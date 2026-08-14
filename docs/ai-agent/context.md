# Context for AI coding agents

Read `docs/developer/architecture.md` first -- this file only adds what's
specific to working on this project as an agent.

## Current state (as of this writing)

Built: the deterministic operational layer (`app/db.py`, `app/models.py`,
`app/repository.py`) plus DB tooling (`db/schema_and_seed.sql`,
`scripts/build_db.py`, `scripts/demo.py`), and a first conversational layer
(`app/llm.py`, `app/llm_models.py`, `app/intent.py`, `app/conversation.py`,
`scripts/chat_demo.py`).

Not built: conversation state persistence (stateless per-call right now --
see architecture.md), any web/API surface, automated tests, the optional
scheduling-engine extension.

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
  `app/conversation.py` recomputes feasible slots fresh from
  `app/repository.py` on every turn and matches a driver's chosen option
  against that fresh list; it never trusts a slot reference against
  something computed in an earlier turn or invented by the model.
- **Replies are template strings around real query results, not
  LLM-phrased**, to keep the LLM surface to exactly one call per message
  and avoid a second place for it to invent something. If this needs to
  read less mechanical later, that's a deliberate second LLM call to add,
  not a reason to let the first call free-form the response.
- **`app/conversation.py` is currently stateless per call.** It re-derives
  the driver's shipment(s) and recomputes options from the DB every time --
  it does not yet read/write `chat_threads`/`chat_messages`. A driver
  saying "take the second one" only resolves against options computed
  *within that same call*. Don't assume cross-turn memory exists until
  that's built.
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
