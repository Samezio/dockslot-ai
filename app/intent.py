"""Turn one driver chat message into structured intent via the LLM.

This is the entire LLM surface for now: one call, one Pydantic-validated
output. It does not look at the database and does not decide anything
operational -- app/conversation.py takes this output and calls
app/repository.py (deterministic) to act on it. See CLAUDE.md section 6.
"""
from app.llm import get_chat_model
from app.llm_models import DriverMessageIntent

_SYSTEM_PROMPT = """\
You are the intent-extraction step for SetuHaul, a freight logistics company. \
Drivers send short, informal chat messages about delays, dock appointments \
and warehouse arrivals. Extract structured facts ONLY from what the driver \
actually wrote.

Rules:
- Never invent a shipment reference, time or number the driver didn't give.
- "Around 12:45" or "about 6:30" IS a specific time (extract "12:45" / \
"18:30") -- "around"/"about" here just means approximate, not unknown. \
Only leave declared_eta_local_time null when the driver gave no usable \
clock time or duration at all (e.g. "soon", "a while", "not sure yet").
- If the driver's message doesn't require any of the missing fields (e.g. \
they're just asking a question), leave those fields null.
- Today's operational date is 2026-08-04 (Asia/Kolkata). Convert relative \
times ("in 45 minutes", "by 9 tonight") to a plausible 24-hour HH:MM \
whenever the message gives you enough to do that -- prefer extracting \
over asking.
- A message that reports a delay/new ETA and/or asks what slots are \
available is REPORT_DELAY or ASK_SLOT_OPTIONS, even if it's phrased as a \
question ("can I still get a slot?", "can the reefer unload tonight?"). \
Reserve GENERAL_QUESTION and UNKNOWN for messages that aren't about a \
delay or slot need at all.
"""


def extract_intent(message_text: str, shipment_context: str = None) -> DriverMessageIntent:
    """Call the LLM once and return validated structured intent.

    shipment_context is an optional short plain-text summary of the
    driver's current shipment (status, current appointment, latest known
    ETA) -- passing it lets the model reason about what's actually
    missing instead of asking generic questions.
    """
    llm = get_chat_model()
    structured_llm = llm.with_structured_output(DriverMessageIntent)

    user_content = "Driver message:\n{}".format(message_text)
    if shipment_context:
        user_content += "\n\nKnown shipment context (for reference only, not driver input):\n{}".format(
            shipment_context
        )

    return structured_llm.invoke(
        [
            {"role": "system", "content": _SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
    )
