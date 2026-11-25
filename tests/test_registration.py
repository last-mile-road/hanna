"""The country entry page, the root redirect, and the closed desktop registration."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

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


def test_country_entry_page_is_public():
    page = TestClient(app).get("/c/mozambique")
    assert page.status_code == 200
    assert "MoH ID" in page.text


def test_root_sends_a_stranger_to_sign_in():
    response = TestClient(app).get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_desktop_registration_is_invitation_only():
    response = TestClient(app).post(
        "/auth/register",
        json={
            "country": "mozambique",
            "email": f"reg-{uuid.uuid4().hex[:8]}@example.test",
            "password": "correct horse battery",
            "display_name": "Stranger",
            "role": "tester",
        },
    )
    assert response.status_code == 403
