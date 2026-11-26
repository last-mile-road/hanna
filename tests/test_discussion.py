"""Discussion endpoint: auth required, channels and links assembled."""

from __future__ import annotations

import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core import auth
from core.db import connect
from server import portal_routes
from server.app import app


def _db_available() -> bool:
    try:
        with connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="development database not running")


def test_discussion_requires_sign_in():
    assert TestClient(app).get("/portal/api/discussion").status_code == 401


def test_discussion_lists_the_two_channels(monkeypatch):
    monkeypatch.setattr(portal_routes, "_topics", lambda *a, **k: [{"name": "run form16"}])
    with connect() as conn:
        country_id = conn.execute(
            text("select id from countries where code='mozambique'")
        ).scalar()
        email = f"disc-{uuid.uuid4().hex[:8]}@example.test"
        row = auth.create_account(
            conn, email=email, password="correct horse battery", display_name="Disc",
            country_id=country_id, role="contributor",
        )
        conn.commit()
    client = TestClient(app)
    assert client.post("/auth/login", json={"email": email, "password": "correct horse battery"}).status_code == 200
    try:
        data = client.get("/portal/api/discussion").json()
        names = [c["name"] for c in data["channels"]]
        assert names == ["general", "mozambique"]
        assert data["channels"][0]["topics"][0]["name"] == "run form16"
        assert "#narrow/channel/" in data["channels"][0]["url"]
    finally:
        with connect() as conn:
            conn.execute(text("delete from audit_log where account_id=:a"), {"a": row["id"]})
            conn.execute(text("delete from accounts where id=:a"), {"a": row["id"]})
            conn.commit()
