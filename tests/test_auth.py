"""Accounts and sessions: hashing, login, the cookie, logout.

Database tests skip when the development PostgreSQL is not running.
"""

from __future__ import annotations

import hashlib
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core import auth
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


def test_password_hash_roundtrip():
    stored = auth.hash_password(PASSWORD)
    assert stored.startswith("$argon2")
    assert auth.verify_password(stored, PASSWORD)
    assert not auth.verify_password(stored, PASSWORD + "x")


@pytest.fixture()
def account():
    email = f"auth-test-{uuid.uuid4().hex[:10]}@example.test"
    with connect() as conn:
        row = auth.create_account(
            conn,
            email=email,
            password=PASSWORD,
            display_name="Auth Test",
            country_id=None,
            role="project_admin",
        )
        conn.commit()
    yield email, row["id"]
    with connect() as conn:
        conn.execute(text("delete from audit_log where account_id = :id"), {"id": row["id"]})
        conn.execute(text("delete from accounts where id = :id"), {"id": row["id"]})
        conn.commit()


def test_login_me_logout(account):
    email, account_id = account
    client = TestClient(app)

    assert client.get("/auth/me").status_code == 401

    bad = client.post("/auth/login", json={"email": email, "password": "wrong password"})
    assert bad.status_code == 401

    ok = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert ok.status_code == 200
    assert ok.json()["account"]["email"] == email
    cookie = client.cookies.get("hanna_session")
    assert cookie

    # the database stores the hash, never the cookie value
    with connect() as conn:
        row = conn.execute(
            text("select id from sessions where account_id = :id"), {"id": account_id}
        ).fetchone()
    assert row and row[0] == hashlib.sha256(cookie.encode()).hexdigest()

    me = client.get("/auth/me")
    assert me.status_code == 200
    assert me.json()["account"]["memberships"][0]["role"] == "project_admin"

    assert client.post("/auth/logout").status_code == 200
    assert client.get("/auth/me").status_code == 401


def test_login_is_case_insensitive(account):
    email, _ = account
    client = TestClient(app)
    response = client.post("/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert response.status_code == 200


def test_five_failures_lock_the_account(account):
    email, _ = account
    client = TestClient(app)
    for _ in range(5):
        assert client.post(
            "/auth/login", json={"email": email, "password": "wrong password"}
        ).status_code == 401
    locked = client.post("/auth/login", json={"email": email, "password": PASSWORD})
    assert locked.status_code == 429
