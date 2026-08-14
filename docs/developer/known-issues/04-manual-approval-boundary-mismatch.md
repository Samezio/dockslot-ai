# 04 -- Last-new-start rule is never enforced by the scheduler

**Severity: Medium.** Confirmed against real seed data. Not fixed.

Originally spotted as a cosmetic `>` vs `>=` inconsistency between two
layers. Checking the actual data showed it's worse than that: on this
facility's data the rule is enforced by one layer and **completely inert
in the other**.

## The rule

`facility_rules` holds a `LAST_NEW_START_TIME` for FAC-JAI-01 of
**21:00** -- a new unload should not *start* at or after that time
without a human approving it. Both layers implement this, independently.

## The two implementations disagree

`app/repository.py:195-201` (chat path) -- **`>=`**:

```python
if last_new_start is not None:
    start_time_of_day = row["slot_start_ts"].split("T")[1][:5]
    if start_time_of_day >= last_new_start:
        needs_manual_approval = True
```

`app/scheduling.py:375` (facility path) -- **`>`**:

```python
needs_manual = snapshot.last_new_start_minutes is not None and start_m > snapshot.last_new_start_minutes
```

The scheduler's own field comment (`app/scheduling.py:104`) says
`# starts at/after the facility's last-new-start rule` -- i.e. it
documents `>=` and implements `>`. The code contradicts its own comment,
which is a good sign this was an oversight rather than a decision.

## Why the difference is total, not marginal

A boundary-condition mismatch normally only matters for the exact
boundary value. Here, **the boundary value is the only value that
exists**. Every slot at or after 21:00 at this facility starts at exactly
21:00 -- there are none later:

```powershell
python -c @'
import sys; sys.path.insert(0,'.')
from app.db import get_connection
c = get_connection()
for r in c.execute("""SELECT slot_id, dock_id, substr(slot_start_ts,12,5) t
                      FROM appointment_slots
                      WHERE facility_id='FAC-JAI-01' AND substr(slot_start_ts,12,5) >= '21:00'
                      ORDER BY t"""):
    print(dict(r))
'@
```

```
{'slot_id': 'SLOT-JAI-014', 'dock_id': 'DOCK-JAI-D1', 't': '21:00'}
{'slot_id': 'SLOT-JAI-028', 'dock_id': 'DOCK-JAI-D2', 't': '21:00'}
{'slot_id': 'SLOT-JAI-042', 'dock_id': 'DOCK-JAI-D3', 't': '21:00'}
{'slot_id': 'SLOT-JAI-056', 'dock_id': 'DOCK-JAI-D4', 't': '21:00'}
{'slot_id': 'SLOT-JAI-070', 'dock_id': 'DOCK-JAI-D5', 't': '21:00'}
```

So `> 21:00` matches nothing that can ever occur, and the scheduler's
manual-approval flag is dead code on this data. The chat layer, using
`>=`, flags all five correctly.

## Side-by-side proof

```powershell
python -c @'
import sys; sys.path.insert(0,'.')
from app.db import get_connection
from app.scheduling import build_facility_snapshot, solve_schedule
from app.repository import find_feasible_slots
c = get_connection()
snap = build_facility_snapshot(c, 'FAC-JAI-01')
res = solve_schedule(snap)
print('last_new_start_minutes =', snap.last_new_start_minutes, '(21:00)')
print('scheduler flagged:', sum(1 for a in res.assignments if a.needs_manual_approval), 'of', len(res.assignments))
for o in find_feasible_slots(c, 'SHP1012', after_ts='2026-08-04T20:00:00+05:30', limit=10):
    print('chat layer:', o.slot_id, o.slot_start_ts[11:16], 'manual_approval =', o.needs_manual_approval)
'@
```

```
last_new_start_minutes = 1260 (21:00)
scheduler flagged: 0 of 15
chat layer: SLOT-JAI-013 20:00 manual_approval = False
chat layer: SLOT-JAI-027 20:00 manual_approval = False
chat layer: SLOT-JAI-041 20:00 manual_approval = False
chat layer: SLOT-JAI-055 20:00 manual_approval = False
chat layer: SLOT-JAI-014 21:00 manual_approval = True
chat layer: SLOT-JAI-028 21:00 manual_approval = True
chat layer: SLOT-JAI-042 21:00 manual_approval = True
chat layer: SLOT-JAI-056 21:00 manual_approval = True
```

Same facility, same rule, same 21:00 slots: the chat layer requires human
approval, the scheduler says none of its 15 assignments need any.

Note the scheduler's `0 of 15` is partly because no assignment lands that
late in this particular solve -- but the point stands: even when one
does, at 21:00 exactly, `>` will not flag it.

## Why it matters

This is a **human-control** rule (brief §9.3): the whole point is that a
late unload gets a person's sign-off rather than being auto-approved. A
silently inert safety flag is worse than an absent one, because the field
exists in the API response (`needs_manual_approval: false`) and reads as
a positive assurance that no approval is required.

It's also a correctness trap for anyone comparing the two layers'
answers: they will disagree on identical inputs, and the scheduler's
answer looks like the permissive one.

## Suggested fix

One character, plus a test:

```python
# app/scheduling.py:375
needs_manual = snapshot.last_new_start_minutes is not None and start_m >= snapshot.last_new_start_minutes
```

Then add a boundary test. The existing test
(`test_late_start_after_last_new_start_flagged_for_manual_approval`) uses
`last_new_start_minutes=100` with a start well past it, so `>` and `>=`
agree and it passes either way -- that's exactly why this survived. A
test asserting the *equal* case would have caught it:

```python
# start_minutes == last_new_start_minutes must still require approval
```

Also worth doing: pull the comparison into one shared helper so the two
layers can't drift again. Right now the rule is implemented twice, in two
different units (`"HH:MM"` string compare vs minutes-since-midnight int
compare), which is what allowed them to diverge silently.
