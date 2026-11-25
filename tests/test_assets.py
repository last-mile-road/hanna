"""Country resources: upload, list, download, delete, and country isolation."""

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


def _account(conn, country_id: int, role: str = "contributor") -> tuple[str, str]:
    email = f"asset-{uuid.uuid4().hex[:10]}@example.test"
    row = auth.create_account(
        conn,
        email=email,
        password=PASSWORD,
        display_name="Asset Tester",
        country_id=country_id,
        role=role,
    )
    return email, row["id"]


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path / "uploads")
    with connect() as conn:
        country_id = _country(conn, "mozambique")
        email, account_id = _account(conn, country_id)
        conn.commit()
    client = TestClient(app)
    assert client.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    yield client, country_id, account_id
    with connect() as conn:
        conn.execute(text("delete from country_assets where country_id = :c"), {"c": country_id})
        conn.execute(text("delete from audit_log where account_id = :a"), {"a": account_id})
        conn.execute(text("delete from accounts where id = :a"), {"a": account_id})
        conn.commit()


def _upload(client, name="places.csv", kind="location", payload=b"district,locality\n"):
    return client.post(
        "/portal/api/assets",
        data={"kind": kind},
        files={"file": (name, payload, "text/csv")},
    )


def test_upload_list_download_delete(workspace):
    client, _, _ = workspace
    created = _upload(client)
    assert created.status_code == 201, created.text
    asset = created.json()["asset"]
    assert asset["kind"] == "location"
    assert asset["filename"] == "places.csv"

    listing = client.get("/portal/api/assets").json()["assets"]
    assert [a["id"] for a in listing] == [asset["id"]]

    download = client.get(f"/portal/api/assets/{asset['id']}/download")
    assert download.status_code == 200
    assert download.content == b"district,locality\n"

    assert client.delete(f"/portal/api/assets/{asset['id']}").status_code == 200
    assert client.get("/portal/api/assets").json()["assets"] == []
    assert client.get(f"/portal/api/assets/{asset['id']}/download").status_code == 404


def test_file_type_is_restricted(workspace):
    client, _, _ = workspace
    assert _upload(client, name="evil.exe", payload=b"MZ").status_code == 422


def test_another_country_cannot_read_the_asset(workspace):
    client, _, _ = workspace
    asset = _upload(client).json()["asset"]
    with connect() as conn:
        other_country = _country(conn, "zzasset")
        email, account_id = _account(conn, other_country)
        conn.commit()
    other = TestClient(app)
    assert other.post("/auth/login", json={"email": email, "password": PASSWORD}).status_code == 200
    assert other.get("/portal/api/assets").json()["assets"] == []
    assert other.get(f"/portal/api/assets/{asset['id']}/download").status_code == 404
    with connect() as conn:
        conn.execute(text("delete from audit_log where account_id = :a"), {"a": account_id})
        conn.execute(text("delete from accounts where id = :a"), {"a": account_id})
        conn.execute(text("delete from countries where code = 'zzasset'"))
        conn.commit()
