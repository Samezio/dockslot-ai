# Shortcomings

Lower-severity gaps found in the same review. None are wrong *answers*
the way `01`-`05` are -- these are missing handling, inconsistencies, and
latent traps. Grouped in one file because each is small.

---

## S1 -- LLM failure surfaces as a bare HTTP 500

**Where:** `app/api.py:96-104` (`POST /chat`)

`handle_driver_message` calls a live LLM. Any provider failure -- missing
API key, exhausted credits, Ollama not running, network timeout --
propagates uncaught:

```powershell
# with extract_intent patched to raise RuntimeError('provider is down')
POST /chat -> HTTP 500 Internal Server Error
```

The UI then renders `Error: Internal Server Error`. This is the *most
likely* failure in day-to-day use (all three of those causes are routine
in dev), and it produces the least actionable message possible.

**Fix:** catch provider exceptions in the route and return `502`/`503`
with a message naming the configured provider, e.g. "LLM provider
'ollama' is not reachable". Keep the detail generic enough not to leak
key material.

---

## S2 -- `/chat` doesn't check `driver_status`; `/identify` does

**Where:** `app/api.py:96-104` vs `app/api.py:73-78`

`/identify` refuses non-ACTIVE drivers with a 403. `/chat` only checks
the driver *exists* (`get_driver`). Since `driver_id` is supplied by the
client, a SUSPENDED or OFF_DUTY driver can skip `/identify` and chat
directly.

The schema explicitly allows those states:

```sql
driver_status TEXT NOT NULL DEFAULT 'ACTIVE'
    CHECK (driver_status IN ('ACTIVE','OFF_DUTY','SUSPENDED','INACTIVE')),
```

Currently latent -- all 15 seeded drivers are ACTIVE.

**Fix:** apply the same status check in `/chat`. Note this is *not* an
authentication fix (see architecture.md -- there is deliberately no auth
yet); it's just making the two endpoints agree on the same rule.

---

## S3 -- UI injects server data via `innerHTML`

**Where:** `web/index.html:181` (driver name) and `:260` (schedule rows)

```js
driverInfo.innerHTML = '<strong>' + body.driver_name + '</strong> ...'
tr.innerHTML = '<td>' + a.shipment_id + '</td>...'
```

`addBubble` correctly uses `textContent` for chat messages, so the file
is internally inconsistent. Risk is low today (values are DB-controlled
IDs and names, not user-submitted free text), but driver names are the
kind of field that eventually becomes editable.

**Fix:** build those rows with `textContent`/`createElement` like
`addBubble` already does.

---

## S4 -- Dock outages with no end time are ignored entirely

**Where:** `app/scheduling.py:173-177`

```sql
SELECT dock_id, event_type, event_start_ts, event_end_ts FROM dock_status_events
WHERE dock_id IN (...) AND event_end_ts IS NOT NULL
```

The schema explicitly permits an open-ended event:

```sql
event_end_ts TEXT,                                    -- nullable
CHECK (event_end_ts IS NULL OR event_end_ts > event_start_ts)
```

An open-ended event is the natural encoding of "this dock broke and we
don't know when it's back" -- precisely the case where scheduling trucks
into it is worst. Today such a dock is treated as fully free.

Untested because all three seeded events happen to have an end time.

**Fix:** treat `event_end_ts IS NULL` as blocking through end-of-day
(and reconsider whether `docks.dock_status` should also be consulted for
currently-broken docks).

---

## S5 -- `REOPENED` events are treated as blocking

**Where:** same query as S4 -- it filters no `event_type` at all.

```sql
CHECK (event_type IN ('MAINTENANCE','BREAKDOWN','CAPACITY_REDUCTION','REOPENED','MANUAL_BLOCK'))
```

`REOPENED` means the dock came *back into service*. Blocking the dock for
the duration of its own reopening event is backwards. Latent -- no
`REOPENED` row in the seed data.

**Fix:** filter to the genuinely blocking types
(`MAINTENANCE`, `BREAKDOWN`, `CAPACITY_REDUCTION`, `MANUAL_BLOCK`), or
better, process events as a status timeline rather than a set of
independent blocks.

Related: `CAPACITY_REDUCTION` is currently treated as a *full* block. It
means reduced throughput, not closure. Modeling it as total unavailability
is conservative (safe direction) but pessimistic -- worth a comment at
minimum so the simplification is explicit.

---

## S6 -- Timestamp comparisons are string comparisons

**Where:** `app/conversation.py:211`, `app/repository.py:157`

```python
and effective_after <= shipment.current_slot_start_ts   # ISO strings
```
```sql
AND slot_start_ts >= ?                                  -- ISO strings
```

Lexicographic comparison of ISO-8601 strings is only equivalent to
chronological comparison while **every** timestamp shares one offset
format. Everything is `+05:30` today, so it works. A `Z`-suffixed or
differently-offset timestamp would compare *wrong* rather than raise --
a silent-wrong-answer failure mode.

**Fix:** parse to `datetime` before comparing (`datetime.fromisoformat`
is already used elsewhere in `app/repository.py`), or document the
invariant loudly at both sites. Low urgency while the data is
single-timezone, but this is the kind of assumption that outlives the
knowledge of it.

---

## S7 -- A load can't span consecutive slots

**Where:** `app/repository.py:190-191`

```python
if _slot_duration_min(row["slot_start_ts"], row["slot_end_ts"]) < shipment.expected_unload_min:
    continue
```

Any shipment whose unload is longer than a single slot is simply
unbookable -- there's no concept of reserving two adjacent slots. The
grid is 60-minute slots, so anything over 60 minutes of unload has no
options at all, and the driver gets the "no feasible slot, escalating"
path.

That escalation is at least the *safe* outcome (a human sees it), so this
is a capability gap rather than a correctness bug. Worth knowing before
someone adds a 90-minute unload to the seed data and concludes the
feasibility logic is broken.

---

## S8 -- Cross-midnight is not modeled

**Where:** `app/scheduling.py:120-124`

```python
def _ts_to_minutes(ts): return _time_to_minutes(ts.split("T")[1][:5])
```

The date is discarded, so a next-day ETA collapses to a small
minutes-since-midnight value and looks like early *this* morning.

This is already documented as an intentional simplification (the seed
data is a single frozen operational day, and the docstring says so).
Listed here only so it isn't rediscovered as a bug -- it becomes real
work the moment the data spans more than one day.
