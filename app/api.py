"""REST API surface over the existing layers (CLAUDE.md section 13:
FastAPI, Pydantic request/response models, explicit contracts, clear
error handling, appropriate HTTP status codes).

This is a thin HTTP wrapper -- it adds no business logic of its own.
Every route calls straight into app/repository.py, app/conversation.py,
or app/scheduling.py, the same functions scripts/chat.py and
scripts/scheduling_demo.py already use.

No authentication. This is a local/dev tool, same as the rest of the
project so far -- don't expose this to an untrusted network without
adding real auth first.

The optional facility-wide scheduling engine is exposed as an explicit,
on-demand endpoint (GET /schedule/{facility_id}). Calling it does NOT
happen automatically from /chat -- a driver's message still only affects
that one shipment, exactly as before. See app/scheduling.py's own
docstring for why that boundary is deliberate.
"""
import sqlite3
from pathlib import Path
from typing import Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import FileResponse

from app.api_schemas import (
    ChatRequest,
    ChatResponse,
    IdentifyRequest,
    IdentifyResponse,
    ScheduleResponse,
    ScheduledAssignmentOut,
    ShipmentOut,
)
from app.conversation import handle_driver_message
from app.db import get_connection
from app.repository import find_driver_by_phone, find_shipments_for_driver, get_driver
from app.scheduling import build_facility_snapshot, solve_schedule

WEB_DIR = Path(__file__).resolve().parent.parent / "web"

app = FastAPI(title="dockslot-ai", version="0.1.0")


def get_db() -> Iterator[sqlite3.Connection]:
    """One connection per request -- simplest option for a single-process
    dev tool; the DB's own concurrency guard (partial unique indexes) is
    what actually keeps concurrent bookings safe, not connection reuse."""
    conn = get_connection()
    try:
        yield conn
    finally:
        conn.close()


@app.get("/", include_in_schema=False)
def index():
    """Driver-facing chat."""
    return FileResponse(WEB_DIR / "index.html")


@app.get("/ops", include_in_schema=False)
def ops():
    """Dispatcher-facing facility schedule. Deliberately a separate page:
    it shows every truck at a facility, which is neither a driver's
    question nor a driver's business."""
    return FileResponse(WEB_DIR / "ops.html")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/identify", response_model=IdentifyResponse)
def identify(payload: IdentifyRequest, conn: sqlite3.Connection = Depends(get_db)):
    """Look up a driver by phone number -- the same identity check
    scripts/chat.py does interactively (no hardcoded default driver)."""
    driver = find_driver_by_phone(conn, payload.phone)
    if driver is None:
        raise HTTPException(status_code=404, detail="No driver found with that phone number.")
    if driver.driver_status != "ACTIVE":
        raise HTTPException(
            status_code=403,
            detail="This account is {}. Please contact operations directly.".format(driver.driver_status),
        )

    shipments = find_shipments_for_driver(conn, driver.driver_id)
    return IdentifyResponse(
        driver_id=driver.driver_id,
        driver_name=driver.driver_name,
        shipments=[
            ShipmentOut(
                shipment_id=s.shipment_id,
                current_status=s.current_status,
                destination_facility_id=s.destination_facility_id,
                effective_eta_ts=s.effective_eta_ts,
            )
            for s in shipments
        ],
    )


@app.post("/chat", response_model=ChatResponse)
def chat(payload: ChatRequest, conn: sqlite3.Connection = Depends(get_db)):
    """One driver chat turn. driver_id must come from a prior /identify --
    this only checks the id is a real driver, it doesn't re-verify phone
    ownership (no auth, see module docstring)."""
    if get_driver(conn, payload.driver_id) is None:
        raise HTTPException(status_code=404, detail="Unknown driver_id. Identify first via POST /identify.")
    reply = handle_driver_message(conn, payload.driver_id, payload.message)
    return ChatResponse(reply=reply)


@app.get("/schedule/{facility_id}", response_model=ScheduleResponse)
def schedule(facility_id: str, conn: sqlite3.Connection = Depends(get_db)):
    """On-demand facility-wide schedule proposal (brief section 7.3).
    Read-only -- solving does not write anything to the database."""
    try:
        snapshot = build_facility_snapshot(conn, facility_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))

    result = solve_schedule(snapshot)
    return ScheduleResponse(
        facility_id=facility_id,
        status=result.status,
        objective_value=result.objective_value,
        assignments=[
            ScheduledAssignmentOut(
                shipment_id=a.shipment_id,
                dock_id=a.dock_id,
                start_minutes=a.start_minutes,
                end_minutes=a.end_minutes,
                needs_manual_approval=a.needs_manual_approval,
            )
            for a in result.assignments
        ],
        unscheduled_shipment_ids=result.unscheduled_shipment_ids,
    )
