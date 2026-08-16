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


# --- Admin portal (app/admin_auth.py, /admin/api/* in app/api.py) -------


class AdminLoginRequest(BaseModel):
    username: str
    password: str


class ShipmentListItemOut(BaseModel):
    shipment_id: str
    order_reference: str
    current_status: str
    priority_code: str
    destination_facility_id: str
    facility_name: str
    driver_id: str
    driver_name: str
    effective_eta_ts: str
    eta_confidence: str


class ShipmentDetailOut(BaseModel):
    shipment_id: str
    order_reference: str
    current_status: str
    priority_code: str
    required_dock_type: str
    temperature_control_required: bool
    load_weight_kg: int
    pallet_count: Optional[int]
    customer_name: str
    product_category: str
    origin_name: str
    origin_city: str
    driver_id: str
    driver_name: str
    driver_phone: str
    driver_status: str
    registration_number: str
    vehicle_type_code: str
    destination_facility_id: str
    facility_name: str
    facility_city: str
    facility_state: str
    effective_eta_ts: str
    eta_source: str
    eta_confidence: str
    eta_note: Optional[str]
    delay_reason_code: Optional[str]
    appointment_id: Optional[str]
    appointment_status: Optional[str]
    booking_source: Optional[str]
    slot_start_ts: Optional[str]
    slot_end_ts: Optional[str]
    dock_code: Optional[str]
    gate_in_ts: Optional[str]
    queue_state: Optional[str]
    queue_position: Optional[int]
    arrival_state: Optional[str]


class DriverListItemOut(BaseModel):
    driver_id: str
    driver_name: str
    phone: str
    driver_status: str
    home_base_city: Optional[str]
    carrier_id: str
    carrier_name: str


class DriverShipmentOut(BaseModel):
    shipment_id: str
    order_reference: str
    current_status: str
    destination_facility_id: str
    facility_name: str
    priority_code: str
    effective_eta_ts: str


class DriverDetailOut(BaseModel):
    driver_id: str
    driver_name: str
    phone: str
    licence_number: str
    home_base_city: Optional[str]
    driver_status: str
    carrier_id: str
    carrier_name: str
    shipments: List[DriverShipmentOut]


class DockOut(BaseModel):
    dock_id: str
    dock_code: str
    dock_type: str
    supports_refrigerated: bool
    max_vehicle_weight_kg: int
    dock_status: str


class FacilityContactOut(BaseModel):
    contact_role: str
    contact_name: str
    email: Optional[str]
    phone: Optional[str]


class FacilityListItemOut(BaseModel):
    facility_id: str
    facility_name: str
    city: str
    state: str
    active_flag: bool


class FacilityDetailOut(BaseModel):
    facility_id: str
    facility_name: str
    city: str
    state: str
    open_time: str
    close_time: str
    checkin_grace_min: int
    default_unload_min: int
    active_flag: bool
    docks: List[DockOut]
    contacts: List[FacilityContactOut]


class RescheduleOut(BaseModel):
    appointment_id: str
    shipment_id: str
    order_reference: str
    driver_name: str
    booking_source: str
    booked_at: str
    old_dock_code: str
    old_slot_start_ts: str
    old_slot_end_ts: str
    new_dock_code: str
    new_slot_start_ts: str
    new_slot_end_ts: str
