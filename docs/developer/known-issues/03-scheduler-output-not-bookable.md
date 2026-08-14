# 03 -- Scheduler output can't actually be booked

**Severity: High.** Confirmed against real seed data. Not fixed.

## What happens

`app/scheduling.py` reasons in **continuous minutes**: a job may start at
any minute the solver likes. The rest of the system reasons in a
**discrete slot grid** -- `appointment_slots` rows with fixed
start/end times, and `propose_booking(conn, shipment_id, slot_id)`
requires a real `slot_id`.

`ScheduledAssignment` carries `dock_id`, `start_minutes`, `end_minutes`
-- and **no `slot_id`**. So a proposal cannot be handed to the booking
layer, and most proposals don't even correspond to a bookable time.

## Reproduction

```powershell
python -c @'
import sys; sys.path.insert(0,'.')
from app.db import get_connection
from app.scheduling import build_facility_snapshot, solve_schedule
c = get_connection()
res = solve_schedule(build_facility_snapshot(c, 'FAC-JAI-01'))
fmt = lambda m: '%02d:%02d' % (m//60, m%60)
match = nomatch = 0
for a in res.assignments:
    r = c.execute('SELECT slot_id FROM appointment_slots WHERE dock_id=? AND substr(slot_start_ts,12,5)=?',
                  (a.dock_id, fmt(a.start_minutes))).fetchone()
    if r: match += 1
    else:
        nomatch += 1
        print('  no slot exists at', a.dock_id, fmt(a.start_minutes), 'for', a.shipment_id)
print('bookable:', match, '| not bookable:', nomatch)
'@
```

Output -- **12 of 15 proposals land on a time with no slot at all**:

```
  no slot exists at DOCK-JAI-D4 12:40 for SHP1018
  no slot exists at DOCK-JAI-D1 09:50 for SHP1007
  no slot exists at DOCK-JAI-D1 10:50 for SHP1009
  no slot exists at DOCK-JAI-D5 10:55 for SHP1010
  no slot exists at DOCK-JAI-D6 10:45 for SHP1011
  no slot exists at DOCK-JAI-D1 12:05 for SHP1012
  no slot exists at DOCK-JAI-D4 11:25 for SHP1014
  no slot exists at DOCK-JAI-D6 12:15 for SHP1016
  no slot exists at DOCK-JAI-D2 12:45 for SHP1017
  no slot exists at DOCK-JAI-D1 08:20 for SHP1003
  no slot exists at DOCK-JAI-D2 09:25 for SHP1004
  no slot exists at DOCK-JAI-D4 09:05 for SHP1005
bookable: 3 | not bookable: 12
```

The 3 that "match" do so by coincidence (their optimal start happened to
land on a grid boundary), not by design.

## Root cause

A genuine modeling mismatch, not a typo. The CP-SAT formulation
deliberately uses continuous time -- that's what makes it a good
scheduling model, and it's why it can pack docks tightly (09:50, 10:45,
11:25). But nothing translates that answer back into the vocabulary the
booking layer speaks.

Note this is *also* why issue 02's moves look so aggressive: shaving 10
minutes off an existing 10:00 booking to start at 09:50 is only
attractive because the model has no idea 09:50 isn't a thing you can
book.

## Consequence

The `/schedule` endpoint is advisory-only, and not obviously so. Nothing
in the API response or the UI says "these times are indicative and mostly
cannot be booked as shown". A dispatcher reading the table would
reasonably assume they could action it.

## Suggested fix

Two coherent directions -- pick one, don't blend:

- **Snap to the slot grid (recommended).** Restrict each job's start to
  the set of real `appointment_slots` starts for its eligible docks, and
  return the `slot_id` alongside each assignment. In CP-SAT this is
  natural: instead of one optional interval per (job, dock), use one per
  (job, slot), with `add_no_overlap` per dock as today. The output then
  feeds straight into `propose_booking`, and the two layers finally agree
  on what a "time" is. Cost: fewer placement options, so slightly worse
  objective values -- but every answer becomes actionable, and issue 02's
  churn drops out naturally.
- **Keep continuous time, label it honestly.** Document and surface the
  output as a capacity/feasibility study rather than a bookable plan, and
  have the UI say so. Cheaper, but leaves the scheduler permanently
  disconnected from the booking path.

The first is more work but is what makes the §7.3 extension actually
usable end-to-end. Worth deciding before building anything else on top of
`/schedule`.
