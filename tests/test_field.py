"""The official portal: register a field user, sign in with the MoH ID, capture gate."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core import settings
from core.db import connect
from server.app import app

MOH = "MOH-" + uuid.uuid4().hex[:8].upper()


def _db_available() -> bool:
    try:
        with connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="development database not running")


@pytest.fixture()
def field_client():
    client = TestClient(app)
    response = client.post(
        "/field/register",
        json={
            "country": "mozambique",
            "moh_id": MOH,
            "display_name": "Field Tester",
            "facility": "Test Health Centre",
            "role": "clinician",
        },
    )
    yield client, response
    with connect() as conn:
        conn.execute(
            text(
                "delete from audit_log where object_type = 'field_user' and object_id in "
                "(select id::text from field_users where moh_id = :m)"
            ),
            {"m": MOH},
        )
        conn.execute(text("delete from field_users where moh_id = :m"), {"m": MOH})
        conn.commit()


def test_register_and_me(field_client):
    client, response = field_client
    assert response.status_code == 200, response.text
    user = response.json()["field_user"]
    assert user["moh_id"] == MOH
    assert user["role"] == "clinician"
    assert client.cookies.get(settings.FIELD_SESSION_COOKIE)

    me = client.get("/field/me")
    assert me.status_code == 200
    assert me.json()["field_user"]["moh_id"] == MOH


def test_logout_then_moh_login(field_client):
    client, _ = field_client
    assert client.post("/field/logout").status_code == 200
    assert client.get("/field/me").status_code == 401

    again = client.post("/field/login", json={"country": "mozambique", "moh_id": MOH})
    assert again.status_code == 200, again.text
    assert client.get("/field/me").status_code == 200


def test_unknown_moh_id_is_refused():
    client = TestClient(app)
    response = client.post(
        "/field/login", json={"country": "mozambique", "moh_id": "NOT-REGISTERED"}
    )
    assert response.status_code == 401


def test_duplicate_registration_is_refused(field_client):
    client, _ = field_client
    fresh = TestClient(app)
    response = fresh.post(
        "/field/register",
        json={
            "country": "mozambique",
            "moh_id": MOH,
            "display_name": "Second Person",
        },
    )
    assert response.status_code == 409


def test_capture_requires_identity_when_switched_on(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    anonymous = TestClient(app)
    blocked = anonymous.post("/api/captures", data={"country": "mozambique"})
    assert blocked.status_code == 401


def test_field_session_passes_the_capture_gate(field_client, monkeypatch):
    client, _ = field_client
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    response = client.post("/api/captures", data={"country": "mozambique"})
    assert response.status_code != 401
