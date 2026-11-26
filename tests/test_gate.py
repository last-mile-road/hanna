"""The API gate: when HANNA_AUTH_REQUIRED is on, nothing private answers anonymously."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core import settings
from core.db import connect
from server.app import app


def _db_available() -> bool:
    try:
        with connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="development database not running")


@pytest.fixture()
def gated(monkeypatch):
    monkeypatch.setattr(settings, "AUTH_REQUIRED", True)
    return TestClient(app)


def test_private_read_is_refused(gated):
    response = gated.get(f"/api/runs/{uuid.uuid4()}")
    assert response.status_code == 401


def test_public_country_list_still_answers(gated):
    assert gated.get("/api/countries").status_code == 200
    assert gated.get("/api/countries/mozambique/qr.png").status_code == 200


def test_capture_page_sends_a_stranger_to_sign_in(gated):
    response = gated.get("/capture", follow_redirects=False)
    assert response.status_code == 307
    assert response.headers["location"].startswith("/login")


def test_field_session_passes_the_gate(gated, monkeypatch):
    moh = "GATE-" + uuid.uuid4().hex[:6].upper()
    client = TestClient(app)
    created = client.post(
        "/field/register",
        json={"country": "mozambique", "moh_id": moh, "display_name": "Gate Tester"},
    )
    assert created.status_code == 200, created.text
    try:
        response = client.get(f"/api/runs/{uuid.uuid4()}")
        assert response.status_code != 401  # the gate let it through; the run is absent
        assert response.status_code == 404
    finally:
        with connect() as conn:
            conn.execute(
                text("delete from audit_log where object_type='field_user' and object_id in "
                     "(select id::text from field_users where moh_id = :m)"), {"m": moh})
            conn.execute(text("delete from field_users where moh_id = :m"), {"m": moh})
            conn.commit()
