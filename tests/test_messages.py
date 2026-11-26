"""Country messages: post, list, empty refused, country isolation."""

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


def _country(conn, code: str) -> int:
    row = conn.execute(text("select id from countries where code = :c"), {"c": code}).scalar()
    if row is None:
        row = conn.execute(
            text("insert into countries (code, name) values (:c, :n) returning id"),
            {"c": code, "n": code.title()},
        ).scalar()
    return row


def _login(country_id: int) -> tuple[TestClient, str, str]:
    email = f"msg-{uuid.uuid4().hex[:10]}@example.test"
    with connect() as conn:
        row = auth.create_account(
            conn, email=email, password=PASSWORD, display_name="Message Tester",
            country_id=country_id, role="contributor",
        )
        conn.commit()
    client = TestClient(app)
    assert client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    return client, email, row["id"]


def _cleanup(email: str, account_id):
    with connect() as conn:
        conn.execute(text("delete from country_messages where author_account_id = :a"), {"a": account_id})
        conn.execute(text("delete from audit_log where account_id = :a"), {"a": account_id})
        conn.execute(text("delete from accounts where id = :a"), {"a": account_id})
        conn.commit()


@pytest.fixture()
def thread(monkeypatch):
    monkeypatch.setattr(settings, "SMTP_HOST", "")
    monkeypatch.setattr(settings, "NOTIFY_EMAIL", "")
    with connect() as conn:
        country_id = _country(conn, "mozambique")
    client, email, account_id = _login(country_id)
    yield client, email, account_id
    _cleanup(email, account_id)


def test_post_and_list(thread):
    client, _, account_id = thread
    created = client.post("/portal/api/messages", json={"body": "The treatment grid reads F as E."})
    assert created.status_code == 201, created.text
    messages = client.get("/portal/api/messages").json()["messages"]
    assert any(m["body"].startswith("The treatment grid") for m in messages)
    assert any(m["author"] == "Message Tester" for m in messages)
    _cleanup_by_account(account_id)


def test_empty_message_is_refused(thread):
    client, _, account_id = thread
    assert client.post("/portal/api/messages", json={"body": "   "}).status_code == 422
    _cleanup_by_account(account_id)


def test_another_country_sees_nothing(thread):
    client, _, account_id = thread
    assert client.post("/portal/api/messages", json={"body": "Private to Mozambique"}).status_code == 201
    with connect() as conn:
        other = _country(conn, "zzmsg")
        conn.commit()
    other_client, other_email, other_id = _login(other)
    assert other_client.get("/portal/api/messages").json()["messages"] == []
    _cleanup(other_email, other_id)
    with connect() as conn:
        conn.execute(text("delete from countries where code = 'zzmsg'"))
        conn.commit()
    _cleanup_by_account(account_id)


def _cleanup_by_account(account_id):
    with connect() as conn:
        conn.execute(text("delete from country_messages where author_account_id = :a"), {"a": account_id})
        conn.commit()
