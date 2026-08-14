"""Plain data shapes returned by the repository layer.

Dataclasses, not Pydantic: this layer only reads/writes structured DB rows,
it never touches free-text LLM output, so there is nothing here that needs
validation beyond what the database's own CHECK/FOREIGN KEY constraints
already enforce. Pydantic earns its place later, at the boundary where we
parse LLM output (see CLAUDE.md section 9 - Structured AI Outputs).
"""
from dataclasses import dataclass
from typing import Optional


@dataclass
class ShipmentSummary:
    shipment_id: str
    driver_id: str
    destination_facility_id: str
    required_dock_type: str
    temperature_control_required: bool
    load_weight_kg: int
    expected_unload_min: int
    priority_code: str
    current_status: str
    effective_eta_ts: str
    eta_source: str
    eta_confidence: str
    current_appointment_id: Optional[str]
    current_slot_id: Optional[str]
    current_slot_start_ts: Optional[str]
    current_slot_end_ts: Optional[str]


@dataclass
class SlotOption:
    slot_id: str
    facility_id: str
    dock_code: str
    dock_type: str
    slot_start_ts: str
    slot_end_ts: str
    needs_manual_approval: bool
    manual_approval_reason: Optional[str]


@dataclass
class BookingResult:
    success: bool
    appointment_id: Optional[str]
    reason: Optional[str]
