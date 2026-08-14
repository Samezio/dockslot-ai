"""Pydantic models for LLM structured output.

Everything here is untrusted external data the moment it comes back from
the model (CLAUDE.md section 9) -- Pydantic validates shape/types, but the
values themselves (a claimed ETA, a claimed shipment reference) still get
checked against the real database before anything acts on them. This
module has no LangChain/provider imports; it only describes the shape
of one LLM call's output.
"""
from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class DriverIntent(str, Enum):
    REPORT_DELAY = "REPORT_DELAY"
    ASK_SLOT_OPTIONS = "ASK_SLOT_OPTIONS"
    CHECK_STATUS = "CHECK_STATUS"
    CHOOSE_OPTION = "CHOOSE_OPTION"
    EARLY_ARRIVAL = "EARLY_ARRIVAL"
    GENERAL_QUESTION = "GENERAL_QUESTION"
    UNKNOWN = "UNKNOWN"


class DriverMessageIntent(BaseModel):
    """What one driver chat message means, extracted -- not acted on."""

    intent: DriverIntent

    mentioned_shipment_reference: Optional[str] = Field(
        None,
        description=(
            "Any shipment/order number or identifying detail the driver explicitly "
            "gave (e.g. 'SHP1006', 'ORD-260804-006'), verbatim. Null if none given."
        ),
    )
    reported_delay_minutes: Optional[int] = Field(
        None,
        ge=0,
        description="A delay duration the driver stated in minutes, if any. Null if not given or not a duration.",
    )
    declared_eta_local_time: Optional[str] = Field(
        None,
        description=(
            "A specific clock time the driver gave for their new arrival, as 24-hour "
            "HH:MM (e.g. '19:10'). Null if the driver gave only a duration or nothing."
        ),
    )
    latest_acceptable_local_time: Optional[str] = Field(
        None,
        description=(
            "A time constraint the driver stated they must be done by / leave by, as "
            "24-hour HH:MM. Null if not given."
        ),
    )
    requested_slot_reference: Optional[str] = Field(
        None,
        description=(
            "If the driver is picking one of the previously offered options (e.g. "
            "'the second one', 'take the 7:30 slot', 'D2 at noon'), a short verbatim "
            "description of which one. Null otherwise."
        ),
    )
    missing_information: List[str] = Field(
        default_factory=list,
        description=(
            "Specific questions that must be asked before this request can be acted on "
            "confidently (e.g. ambiguous ETA, no delay amount given). Empty list if nothing is missing."
        ),
    )
    confidence: str = Field(
        description="LOW, MEDIUM, or HIGH -- how confident the extraction above is."
    )
