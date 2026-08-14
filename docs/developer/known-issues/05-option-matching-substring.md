# 05 -- Option matching is substring-based

**Severity: Medium** (latent on current data, but the failure is a silent
wrong booking). Confirmed by reproduction. Not fixed.

## What happens

`_match_requested_option` (`app/conversation.py:93-117`) resolves a
driver's free-text choice ("the second one", "D2", "7:30") against the
options actually offered. It does so with plain substring containment,
with no check that the match is unambiguous -- so a longer identifier can
be matched by a shorter one that appears inside it.

## Reproduction

```powershell
python -c @'
import sys; sys.path.insert(0,'.')
from app.conversation import _match_requested_option
from app.models import SlotOption
def s(i, code, t): return SlotOption('SLOT%d'%i, 'F', code, 'STD', '2026-08-04T%s:00+05:30'%t, 'x', False, None)
opts = [s(1,'D1','12:00'), s(2,'D2','13:00'), s(3,'D10','14:00')]
for ref in ['the third one', 'D10', 'the last one', 'option 2', '21st']:
    m = _match_requested_option(opts, ref)
    print(repr(ref), '->', (m.slot_id, m.dock_code) if m else None)
'@
```

```
'the third one' -> ('SLOT3', 'D10')
'D10'           -> ('SLOT1', 'D1')     <-- wrong dock, silently
'the last one'  -> None
'option 2'      -> None
'21st'          -> ('SLOT1', 'D1')     <-- '1st' found inside '21st'
```

## Two distinct problems

**a) Wrong match (dangerous).** `"D10"` matches `D1` because the dock-code
loop (`app/conversation.py:108-110`) checks
`if opt.dock_code.lower() in text` and returns the first hit in list
order. A driver asking for dock D10 gets booked into D1 with a
confirmation naming D1 -- no error, no clarification. Same class of
problem for the ordinal loop (`app/conversation.py:103-106`): `"21st"`
contains `"1st"`.

Currently **latent**: this facility's dock codes are `D1`-`D6`, all single
digit, so no code is a prefix of another. It activates the moment a
facility has 10+ docks, which is not an exotic scenario for a logistics
site.

**b) Missed match (safe but annoying).** `"option 2"` and `"the last one"`
match nothing, so the agent replies "I couldn't tell which option you
meant" and re-lists. That's the correct *safe* behavior -- it refuses to
guess, exactly as intended -- but "option 2" is natural phrasing that a
driver will plausibly use.

Worth keeping the distinction clear: (b) is a usability gap, (a) is a
correctness bug. Only (a) needs fixing urgently.

## Why the existing safeguards don't help

The persisted-offer design (`get_last_offered_slot_ids` +
`get_slots_by_ids`) already prevents *ordinal drift* -- "the first one"
always means position 1 of what was actually shown, and availability is
re-verified before booking. That's solid, and it's tested.

But it guarantees the right **list**, not the right **element within
it**. Both the dock-code and ordinal matchers pick an element by
substring, and a wrong-but-available element passes the availability
re-check happily. So this slips past exactly the machinery built to catch
this class of mistake.

## Suggested fix

Make the match exact and ambiguity-aware, rather than first-hit-wins:

1. Tokenize `text` on word boundaries instead of using `in` -- compare
   whole tokens against `dock_code.lower()`, so `"d10"` cannot match
   `"d1"`. Same for the ordinal words (`\b1st\b` won't match `21st`).
2. Collect **all** candidate matches, not the first. If more than one
   distinct option matches, return `None` -- the caller already handles
   that by re-listing and asking, which is the right answer for a genuinely
   ambiguous reference.
3. While in there, add `"option N"` / `"number N"` / `"the last one"` to
   the recognized forms -- cheap, and covers the (b) cases above.

Keep the function's existing philosophy intact: when unsure, return
`None` and let the driver clarify. The bug isn't that it's conservative,
it's that substring matching makes it *accidentally confident*.

Add a regression test with a `D1`/`D10` pair -- no current test uses
multi-digit dock codes, which is why this is invisible today.
