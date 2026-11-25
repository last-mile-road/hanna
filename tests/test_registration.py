"""Self-registration behind a country QR, and the country entry page."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core.db import connect
from server.app import app

PASSWORD = "correct horse battery"


def _db_available() -> bool:
    try:
        with connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="development database not running")


def _country(code: str = "mozambique") -> str | None:
    with connect() as conn:
        return conn.execute(
            text("select code from countries where code = :c"), {"c": code}
        ).scalar()


@pytest.fixture()
def registered():
    email = f"reg-test-{uuid.uuid4().hex[:10]}@example.test"
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "country": "mozambique",
            "email": email,
            "password": PASSWORD,
            "display_name": "Tester One",
            "role": "tester",
            "province": "Capital",
        },
    )
    yield client, email, response
    with connect() as conn:
        conn.execute(
            text(
                "delete from audit_log where account_id in "
                "(select id from accounts where email = :e)"
            ),
            {"e": email},
        )
        conn.execute(text("delete from accounts where email = :e"), {"e": email})
        conn.commit()


def test_country_entry_page_is_public():
    client = TestClient(app)
    page = client.get("/c/mozambique")
    assert page.status_code == 200
    assert "Register" in page.text


def test_root_sends_a_stranger_to_sign_in():
    client = TestClient(app)
    response = client.get("/", follow_redirects=False)
    assert response.status_code in (302, 307)
    assert response.headers["location"] == "/login"


def test_registration_signs_the_tester_in(registered):
    client, email, response = registered
    assert response.status_code == 200, response.text
    account = response.json()["account"]
    assert account["email"] == email
    membership = account["memberships"][0]
    assert membership["role"] == "tester"
    assert membership["country_code"] == "mozambique"
    assert membership["status"] == "active"
    assert membership["province"] == "Capital"

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["account"]["email"] == email


def test_duplicate_email_is_refused():
    email = f"reg-test-{uuid.uuid4().hex[:10]}@example.test"
    client = TestClient(app)
    payload = {
        "country": "mozambique",
        "email": email,
        "password": PASSWORD,
        "display_name": "Twice",
        "role": "tester",
    }
    try:
        assert client.post("/auth/register", json=payload).status_code == 200
        second = client.post("/auth/register", json=payload)
        assert second.status_code == 409
    finally:
        with connect() as conn:
            conn.execute(
                text(
                    "delete from audit_log where account_id in "
                    "(select id from accounts where email = :e)"
                ),
                {"e": email},
            )
            conn.execute(text("delete from accounts where email = :e"), {"e": email})
            conn.commit()


def test_admin_roles_cannot_self_register():
    if _country() is None:
        pytest.skip("mozambique country row not loaded")
    client = TestClient(app)
    response = client.post(
        "/auth/register",
        json={
            "country": "mozambique",
            "email": f"reg-test-{uuid.uuid4().hex[:8]}@example.test",
            "password": PASSWORD,
            "display_name": "Sneaky",
            "role": "country_admin",
        },
    )
    assert response.status_code == 403
