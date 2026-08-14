"""Pydantic request/response models for app/api.py.

Kept separate from app/api.py itself (routes) the same way app/llm_models.py
is kept separate from app/intent.py -- these describe HTTP contract shapes,
not orchestration logic. CLAUDE.md section 13: explicit API contracts.
"""
from typing import List, Optional

from pydantic import BaseModel


class IdentifyRequest(BaseModel):
    phone: str


class ShipmentOut(BaseModel):
    shipment_id: str
    current_status: str
    destination_facility_id: str
    effective_eta_ts: str


class IdentifyResponse(BaseModel):
    driver_id: str
    driver_name: str
    shipments: List[ShipmentOut]


class ChatRequest(BaseModel):
    driver_id: str
    message: str


class ChatResponse(BaseModel):
    reply: str


class ScheduledAssignmentOut(BaseModel):
    shipment_id: str
    dock_id: str
    start_minutes: int
    end_minutes: int
    needs_manual_approval: bool


class ScheduleResponse(BaseModel):
    facility_id: str
    status: str
    objective_value: Optional[float]
    assignments: List[ScheduledAssignmentOut]
    unscheduled_shipment_ids: List[str]
