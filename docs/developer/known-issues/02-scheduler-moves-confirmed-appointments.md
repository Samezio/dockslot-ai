# 02 -- Scheduler silently reassigns CONFIRMED appointments

**Severity: High.** Confirmed against real seed data. Not fixed.

## What happens

`build_facility_snapshot` treats every non-completed shipment as a free
job to be placed wherever the objective prefers. It does not care that a
shipment already has a `CONFIRMED` (or `PENDING_CONFIRMATION`)
appointment -- an existing booking is used only as a soft *due date*
(`app/scheduling.py:220-243`), never as something to preserve.

The result is a proposal that quietly rearranges commitments already made
to drivers and the warehouse, with nothing in the output marking which
assignments are changes.

## Reproduction

```powershell
python -c @'
import sys; sys.path.insert(0,'.')
from app.db import get_connection
from app.scheduling import build_facility_snapshot, solve_schedule
c = get_connection()
res = solve_schedule(build_facility_snapshot(c, 'FAC-JAI-01'))
fmt = lambda m: '%02d:%02d' % (m//60, m%60)
for a in res.assignments:
    r = c.execute("""SELECT ap.appointment_status, d.dock_id, s.slot_start_ts
      FROM appointments ap JOIN appointment_slots s ON s.slot_id=ap.slot_id
      JOIN docks d ON d.dock_id=s.dock_id
      WHERE ap.shipment_id=? AND ap.is_current=1
        AND ap.appointment_status IN ('CONFIRMED','PENDING_CONFIRMATION')""",
      (a.shipment_id,)).fetchone()
    if r and (r['slot_start_ts'].split('T')[1][:5] != fmt(a.start_minutes) or r['dock_id'] != a.dock_id):
        print('MOVED', a.shipment_id, r['appointment_status'], r['dock_id'],
              r['slot_start_ts'].split('T')[1][:5], '->', a.dock_id, fmt(a.start_minutes))
'@
```

Output -- **12 of 15 assignments move an existing booking**:

```
MOVED SHP1020 CONFIRMED            DOCK-JAI-D2 18:00 -> DOCK-JAI-D1 18:00
MOVED SHP1006 CONFIRMED            DOCK-JAI-D4 10:00 -> DOCK-JAI-D1 12:00
MOVED SHP1007 CONFIRMED            DOCK-JAI-D1 10:00 -> DOCK-JAI-D1 09:50
MOVED SHP1009 CONFIRMED            DOCK-JAI-D4 11:00 -> DOCK-JAI-D2 10:50
MOVED SHP1010 CONFIRMED            DOCK-JAI-D5 11:00 -> DOCK-JAI-D5 10:55
MOVED SHP1011 CONFIRMED            DOCK-JAI-D6 11:00 -> DOCK-JAI-D6 10:45
MOVED SHP1013 PENDING_CONFIRMATION DOCK-JAI-D2 11:00 -> DOCK-JAI-D1 11:00
MOVED SHP1014 PENDING_CONFIRMATION DOCK-JAI-D1 11:00 -> DOCK-JAI-D4 11:25
MOVED SHP1017 CONFIRMED            DOCK-JAI-D1 12:00 -> DOCK-JAI-D1 12:45
MOVED SHP1003 CONFIRMED            DOCK-JAI-D1 09:00 -> DOCK-JAI-D1 08:20
MOVED SHP1004 CONFIRMED            DOCK-JAI-D2 09:00 -> DOCK-JAI-D2 09:25
MOVED SHP1005 CONFIRMED            DOCK-JAI-D3 09:00 -> DOCK-JAI-D4 09:05
```

`SHP1006` is the clearest case: a **confirmed** 10:00 appointment at D4
becomes 12:00 at D1 -- a two-hour move, presented identically to a
brand-new assignment.

## Why it matters more now

As a standalone demo script (`scripts/scheduling_demo.py`) this was
defensible: a human reads the output and interprets it. Now that it's
`GET /schedule/{facility_id}` rendering into a UI table, the same data
reads as "today's plan" with no signal that most rows are proposed
*changes* to commitments already made. A dispatcher acting on it would be
rebooking a dozen drivers without being told that's what they're doing.

This also interacts badly with the brief's own distinction (§ on
appointment states) between *offered*, *held*, and *confirmed* -- the
scheduler currently collapses all three into "movable".

## Suggested fix

Decide the intended semantics first -- this is a product question, not
just a code fix:

- **Option A (conservative):** treat current CONFIRMED appointments as
  fixed occupancies (like in-progress unloads), so the scheduler only
  places trucks that genuinely need a slot. Simplest, matches "don't
  break promises", but gives up most optimization headroom.
- **Option B (transparent):** keep full freedom to re-optimize, but add
  `previous_dock_id` / `previous_start_minutes` / `is_change` to
  `ScheduledAssignment`, surface them through the API, and make the UI
  visibly mark changes. Keeps the optimization value, makes the cost
  explicit.
- **Option C (hybrid):** allow moves but add an objective penalty per
  disturbed confirmed appointment, so the solver only moves one when the
  gain is real.

Recommend **B** as the smallest honest fix (the information is already
available at snapshot-build time), with C as a follow-up if churn proves
excessive in practice. A is probably too blunt -- it would defeat the
point of the brief's §7.3 extension.

Either way, add a test asserting the proposal's relationship to existing
appointments; `tests/test_scheduling.py` currently never cross-checks
against the `appointments` table.
