"""REST API surface over the existing layers (CLAUDE.md section 13:
FastAPI, Pydantic request/response models, explicit contracts, clear
error handling, appropriate HTTP status codes).

This is a thin HTTP wrapper -- it adds no business logic of its own.
Every route calls straight into app/repository.py, app/conversation.py,
or app/scheduling.py, the same functions scripts/chat.py and
scripts/scheduling_demo.py already use.

No authentication on the driver/dispatcher routes (/identify, /chat,
/schedule, /ops). This is a local/dev tool, same as the rest of the
project so far -- don't expose it to an untrusted network without adding
real auth first. The /admin portal is the one exception: it sits behind a
login and session cookie (see app/admin_auth.py) since it exposes driver
and shipment data a dispatcher, not just anyone, should see.

The optional facility-wide scheduling engine is exposed as an explicit,
on-demand endpoint (GET /schedule/{facility_id}). Calling it does NOT
happen automatically from /chat -- a driver's message still only affects
that one shipment, exactly as before. See app/scheduling.py's own
docstring for why that boundary is deliberate.
"""
import sqlite3
from pathlib import Path
from typing import Iterator, List, Optional

from fastapi import APIRouter, Cookie, Depends, FastAPI, HTTPException, Response
from fastapi.responses import FileResponse, RedirectResponse

from app import admin_auth, repository
from app.api_schemas import (
    AdminLoginRequest,
    ChatRequest,
    ChatResponse,
    DockOut,
    DriverDetailOut,
    DriverListItemOut,
    DriverShipmentOut,
    FacilityContactOut,
    FacilityDetailOut,
    FacilityListItemOut,
    IdentifyRequest,
    IdentifyResponse,
    RescheduleOut,
    ScheduleResponse,
    ScheduledAssignmentOut,
    ShipmentDetailOut,
    ShipmentListItemOut,
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


@app.get("/admin/login", include_in_schema=False)
def admin_login_page():
    """Public login form. Not gated -- it's what an unauthenticated visit
    to /admin redirects to."""
    return FileResponse(WEB_DIR / "admin_login.html")


@app.post("/admin/login")
def admin_login(payload: AdminLoginRequest, response: Response):
    if not admin_auth.verify_admin_login(payload.username, payload.password):
        raise HTTPException(status_code=401, detail="Invalid username or password.")
    token = admin_auth.create_session()
    response.set_cookie(
        key=admin_auth.SESSION_COOKIE_NAME,
        value=token,
        httponly=True,
        samesite="lax",
        max_age=int(admin_auth.SESSION_TTL.total_seconds()),
    )
    return {"status": "ok"}


@app.post("/admin/logout")
def admin_logout(
    response: Response,
    session_token: Optional[str] = Cookie(default=None, alias=admin_auth.SESSION_COOKIE_NAME),
):
    admin_auth.destroy_session(session_token)
    response.delete_cookie(admin_auth.SESSION_COOKIE_NAME)
    return {"status": "ok"}


@app.get("/admin", include_in_schema=False)
def admin_dashboard(
    session_token: Optional[str] = Cookie(default=None, alias=admin_auth.SESSION_COOKIE_NAME),
):
    """Admin portal: shipments, drivers, docks/facilities, recent
    reschedules. Redirects to the login page (rather than the JSON api's
    plain 401) since a human is loading this directly in a browser."""
    if not admin_auth.is_authenticated(session_token):
        return RedirectResponse(url="/admin/login")
    return FileResponse(WEB_DIR / "admin.html")


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


# --- Admin portal JSON API ------------------------------------------------
# All routes here require a valid admin session (app/admin_auth.py) --
# the dependency is declared once on the router rather than on every route.
admin_api = APIRouter(prefix="/admin/api", dependencies=[Depends(admin_auth.require_admin_session)])


@admin_api.get("/shipments", response_model=List[ShipmentListItemOut])
def admin_list_shipments(
    status: Optional[str] = None,
    facility_id: Optional[str] = None,
    search: Optional[str] = None,
    conn: sqlite3.Connection = Depends(get_db),
):
    rows = repository.list_shipments_for_admin(conn, status=status, facility_id=facility_id, search=search)
    return [ShipmentListItemOut(**dict(row)) for row in rows]


@admin_api.get("/shipments/{shipment_id}", response_model=ShipmentDetailOut)
def admin_get_shipment(shipment_id: str, conn: sqlite3.Connection = Depends(get_db)):
    row = repository.get_shipment_admin_detail(conn, shipment_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown shipment_id.")
    return ShipmentDetailOut(**dict(row))


@admin_api.get("/drivers", response_model=List[DriverListItemOut])
def admin_list_drivers(search: Optional[str] = None, conn: sqlite3.Connection = Depends(get_db)):
    rows = repository.list_drivers_for_admin(conn, search=search)
    return [DriverListItemOut(**dict(row)) for row in rows]


@admin_api.get("/drivers/{driver_id}", response_model=DriverDetailOut)
def admin_get_driver(driver_id: str, conn: sqlite3.Connection = Depends(get_db)):
    row = repository.get_driver_admin_detail(conn, driver_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown driver_id.")
    shipment_rows = repository.list_all_shipments_for_driver(conn, driver_id)
    return DriverDetailOut(
        **dict(row),
        shipments=[DriverShipmentOut(**dict(s)) for s in shipment_rows],
    )


@admin_api.get("/facilities", response_model=List[FacilityListItemOut])
def admin_list_facilities(conn: sqlite3.Connection = Depends(get_db)):
    rows = repository.list_facilities_for_admin(conn)
    return [FacilityListItemOut(**dict(row)) for row in rows]


@admin_api.get("/facilities/{facility_id}", response_model=FacilityDetailOut)
def admin_get_facility(facility_id: str, conn: sqlite3.Connection = Depends(get_db)):
    row = repository.get_facility_admin_detail(conn, facility_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Unknown facility_id.")
    dock_rows = repository.list_docks_for_admin(conn, facility_id)
    contact_rows = repository.list_facility_contacts(conn, facility_id)
    return FacilityDetailOut(
        **dict(row),
        docks=[DockOut(**dict(d)) for d in dock_rows],
        contacts=[FacilityContactOut(**dict(c)) for c in contact_rows],
    )


@admin_api.get("/reschedules/recent", response_model=List[RescheduleOut])
def admin_recent_reschedules(limit: int = 20, conn: sqlite3.Connection = Depends(get_db)):
    """Recent reschedules, computed live from the appointments table --
    nothing is persisted specifically for this view (see
    repository.list_recent_reschedules)."""
    rows = repository.list_recent_reschedules(conn, limit=limit)
    return [RescheduleOut(**dict(row)) for row in rows]


app.include_router(admin_api)
