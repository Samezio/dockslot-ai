# 01 -- Multi-shipment drivers can never use the chat

**Severity: High.** Confirmed by reproduction. Not fixed.

## What happens

When a driver has more than one active shipment, `handle_driver_message`
asks which one they mean -- and then never does anything with the answer.
Every subsequent message re-asks the same question. The driver cannot get
past it, no matter what they reply.

## Reproduction

```powershell
python -c @'
import sys; sys.path.insert(0,'.')
import sqlite3
from pathlib import Path
import app.conversation as conv
from app.llm_models import DriverIntent, DriverMessageIntent

SQL = Path('db/schema_and_seed.sql').read_text(encoding='utf-8')
c = sqlite3.connect(':memory:'); c.row_factory = sqlite3.Row; c.executescript(SQL)

def fake(message_text, shipment_context=None):
    return DriverMessageIntent(intent=DriverIntent.REPORT_DELAY,
        mentioned_shipment_reference='SHP1006',
        declared_eta_local_time='13:00', confidence='HIGH')
conv.extract_intent = fake

print(conv.handle_driver_message(c, 'DRV006', 'Traffic, reaching 13:00')[:70])
print(conv.handle_driver_message(c, 'DRV006', 'SHP1006')[:70])
print(conv.handle_driver_message(c, 'DRV006', 'the first one, SHP1006, reaching 13:00')[:70])
'@
```

Output -- note turn 2 answers with the exact shipment ID and still loops:

```
You have more than one active shipment today. Which one is this about?
You have more than one active shipment today. Which one is this about?
You have more than one active shipment today. Which one is this about?
```

## Root cause

`app/conversation.py:126-134` returns the disambiguation question early
and never revisits it. The field that would resolve it,
`DriverMessageIntent.mentioned_shipment_reference`, is **extracted by the
LLM but read nowhere in the codebase**:

```powershell
PS> Select-String -Path app\*.py, scripts\*.py -Pattern mentioned_shipment_reference
app\llm_models.py:31:    mentioned_shipment_reference: Optional[str] = Field(
```

One hit -- its own definition. The prompt asks the model for it
(`app/intent.py`), Pydantic validates it, and then it's dropped.

There's also no persistence of a resolved choice: ambiguous-driver
replies are deliberately not written to `chat_threads` (a reasonable
decision on its own, see architecture.md), so even a correct answer has
nowhere to be remembered.

## Who it affects

Four of fifteen seeded drivers:

```
DRV003, DRV004, DRV006, DRV007  (2 active shipments each)
```

**DRV006 is the example phone number printed in the UI**
(`web/index.html`, "Seeded example: 9000010006") and in
`docs/developer/development.md`. So the advertised way to try the system
by hand hits this immediately -- `POST /identify` happily returns both
SHP1006 and SHP1021, then `POST /chat` deadlocks.

This is also the brief's own §7.1 ambiguity requirement, currently
half-implemented: it correctly refuses to guess (good -- that part is
deliberate and should stay), but never offers the driver a way to tell
it.

## Suggested fix

Match `intent.mentioned_shipment_reference` against the candidate
shipments before falling back to the question, and persist the resolution
so follow-up turns don't need it repeated:

1. In `handle_driver_message`, when `len(shipments) > 1`, call
   `extract_intent` **first** (it currently short-circuits before the LLM
   call), then filter `shipments` by the reference.
2. If exactly one matches, proceed with it. If zero or several match,
   ask -- the current behavior, which is correct as a fallback.
3. Consider opening the thread once resolved so the choice survives the
   turn.

Note step 1 means the ambiguous path starts costing an LLM call. That's
a real trade-off (today it's free), but there's no way to use what the
driver said without reading it. An alternative that keeps it free: match
the raw `message_text` against the shipment IDs directly before calling
the LLM, and only fall through to the LLM-extracted reference if that
misses.

Add a regression test with DRV006 -- `tests/test_conversation.py`
currently only uses single-shipment drivers, which is why this went
unnoticed.
