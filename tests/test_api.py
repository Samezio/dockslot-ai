"""API-layer tests: check the HTTP wiring (status codes, request/response
shapes, dependency override to a test DB) -- not business logic, which is
already covered by test_repository.py/test_conversation.py/
test_scheduling.py. /chat's LLM call is mocked out the same way
test_conversation.py does it, so these never hit a real provider.
"""
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

import app.conversation as conversation
from app.api import app, get_db
from app.llm_models import DriverIntent, DriverMessageIntent

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_SQL = (REPO_ROOT / "db" / "schema_and_seed.sql").read_text(encoding="utf-8")


def _build_temp_db(path):
    connection = sqlite3.connect(str(path))
    connection.executescript(SCHEMA_SQL)
    connection.commit()
    connection.close()


@pytest.fixture
def client(tmp_path):
    db_path = tmp_path / "api_test.db"
    _build_temp_db(db_path)

    def override_get_db():
        connection = sqlite3.connect(str(db_path))
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON;")
        try:
            yield connection
        finally:
            connection.close()

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


def _mock_intent(intent, **overrides):
    fields = dict(
        intent=intent,
        mentioned_shipment_reference=None,
        reported_delay_minutes=None,
        declared_eta_local_time=None,
        latest_acceptable_local_time=None,
        requested_slot_reference=None,
        missing_information=[],
        confidence="HIGH",
    )
    fields.update(overrides)
    return DriverMessageIntent(**fields)


def test_index_serves_ui(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "dockslot-ai" in r.text


def test_health(client):
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_identify_known_driver(client):
    # DRV006 / +91-9000010006 per db/schema_and_seed.sql.
    r = client.post("/identify", json={"phone": "9000010006"})
    assert r.status_code == 200
    body = r.json()
    assert body["driver_id"] == "DRV006"
    assert body["driver_name"]
    assert isinstance(body["shipments"], list)


def test_identify_unknown_phone(client):
    r = client.post("/identify", json={"phone": "0000000000"})
    assert r.status_code == 404


def test_chat_unknown_driver_id(client):
    r = client.post("/chat", json={"driver_id": "DRV999", "message": "hi"})
    assert r.status_code == 404


def test_chat_check_status(client, monkeypatch):
    # DRV010/SHP1010 has no seeded thread (see tests/test_conversation.py's
    # module docstring) -- a clean slate for a status check.
    monkeypatch.setattr(
        conversation,
        "extract_intent",
        lambda message_text, shipment_context=None: _mock_intent(DriverIntent.CHECK_STATUS),
    )
    r = client.post("/chat", json={"driver_id": "DRV010", "message": "what's my status?"})
    assert r.status_code == 200
    reply = r.json()["reply"].lower()
    assert "appointment" in reply or "status" in reply


def test_schedule_known_facility(client):
    r = client.get("/schedule/FAC-JAI-01")
    assert r.status_code == 200
    body = r.json()
    assert body["facility_id"] == "FAC-JAI-01"
    assert body["status"] in ("OPTIMAL", "FEASIBLE")
    assert isinstance(body["assignments"], list)


def test_schedule_unknown_facility(client):
    r = client.get("/schedule/FAC-NOPE-99")
    assert r.status_code == 404
