"""Team invitations: create, list, accept, revoke."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core import auth, settings
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


def _account(country_id: int, role: str = "country_admin") -> tuple[str, str]:
    email = f"inv-{uuid.uuid4().hex[:10]}@example.test"
    with connect() as conn:
        row = auth.create_account(
            conn, email=email, password=PASSWORD, display_name="Admin",
            country_id=country_id, role=role,
        )
        conn.commit()
    return email, row["id"]


def _cleanup(*emails):
    with connect() as conn:
        for email in emails:
            conn.execute(
                text("delete from audit_log where account_id in "
                     "(select id from accounts where email = :e)"), {"e": email})
            conn.execute(text("delete from accounts where email = :e"), {"e": email})
            conn.execute(text("delete from invitations where email = :e"), {"e": email})
        conn.commit()


@pytest.fixture()
def admin(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "")  # no mail in tests
    with connect() as conn:
        country_id = conn.execute(
            text("select id from countries where code = 'mozambique'")
        ).scalar()
    email, account_id = _account(country_id)
    client = TestClient(app)
    assert client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    yield client, country_id, email
    _cleanup(email)


def test_invite_accept_and_member_list(admin):
    client, country_id, admin_email = admin
    invitee = f"new-{uuid.uuid4().hex[:8]}@example.test"
    created = client.post("/portal/api/invitations", json={"email": invitee, "role": "contributor"})
    assert created.status_code == 201, created.text
    link = created.json()["link"]
    token = link.rstrip("/").split("/")[-1]

    team = client.get("/portal/api/team").json()
    assert any(i["email"] == invitee for i in team["invitations"])

    fresh = TestClient(app)
    accepted = fresh.post(
        f"/auth/invite/{token}",
        json={"display_name": "New Contributor", "password": PASSWORD},
    )
    assert accepted.status_code == 200, accepted.text

    # signed in as the new account, and it sees the country portal
    assert fresh.get("/auth/me").status_code == 200
    assert fresh.get("/portal/api/assets").status_code == 200

    team = client.get("/portal/api/team").json()
    assert any(m["email"] == invitee for m in team["members"])
    _cleanup(invitee)


def test_token_is_single_use(admin):
    client, _, _ = admin
    invitee = f"once-{uuid.uuid4().hex[:8]}@example.test"
    token = client.post(
        "/portal/api/invitations", json={"email": invitee, "role": "tester"}
    ).json()["link"].rstrip("/").split("/")[-1]
    first = TestClient(app).post(
        f"/auth/invite/{token}", json={"display_name": "Once", "password": PASSWORD}
    )
    assert first.status_code == 200
    second = TestClient(app).post(
        f"/auth/invite/{token}", json={"display_name": "Twice", "password": PASSWORD}
    )
    assert second.status_code == 422
    _cleanup(invitee)


def test_a_tester_cannot_invite():
    with connect() as conn:
        country_id = conn.execute(
            text("select id from countries where code = 'mozambique'")
        ).scalar()
    email, _ = _account(country_id, role="tester")
    client = TestClient(app)
    assert client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    assert client.get("/portal/api/team").status_code == 403
    assert client.post(
        "/portal/api/invitations", json={"email": "x@example.test", "role": "viewer"}
    ).status_code == 403
    _cleanup(email)
