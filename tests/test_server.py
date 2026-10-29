"""HTTP surface, Phase 1. No model calls: refusals and routing only."""

from __future__ import annotations

import io

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core.db import connect
from server.app import app

client = TestClient(app)


def _db_available() -> bool:
    try:
        with connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


def test_upload_requires_the_dummy_attestation():
    response = client.post(
        "/api/runs",
        files={"file": ("card.jpg", io.BytesIO(b"x"), "image/jpeg")},
        data={"country": "mozambique", "attested_dummy": "false"},
    )
    assert response.status_code == 422
    assert "cartão de teste" in response.json()["detail"]


def test_upload_rejects_wrong_format():
    response = client.post(
        "/api/runs",
        files={"file": ("card.txt", io.BytesIO(b"x"), "text/plain")},
        data={"country": "mozambique", "attested_dummy": "true"},
    )
    assert response.status_code == 422


def test_unknown_run_is_404():
    assert client.get("/api/runs/not-a-uuid").status_code == 404
    assert client.get("/api/runs/00000000-0000-0000-0000-000000000000").status_code == 404
    assert client.get("/api/runs/00000000-0000-0000-0000-000000000000/events").status_code == 404


def test_image_link_requires_a_signature():
    response = client.get(
        "/api/runs/00000000-0000-0000-0000-000000000000/image/card?sig=bad"
    )
    assert response.status_code == 403


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_healthz_reports_the_database():
    assert client.get("/healthz").json() == {"ok": True}
