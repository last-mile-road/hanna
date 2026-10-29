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


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_reuploading_a_known_card_does_not_repoint_it():
    """Regression: upsert_card updates on md5 conflict, which turned dummy cards into
    uploads and pointed them at a copy under output/uploads. A second upload of the same
    bytes must reuse the row and leave its path and kind alone."""
    import cv2
    import numpy as np
    from sqlalchemy import text as sql

    image = np.full((1600, 1200, 3), 220, dtype=np.uint8)
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    payload = encoded.tobytes()

    def upload(name: str) -> str:
        response = client.post(
            "/api/runs",
            files={"file": (name, io.BytesIO(payload), "image/jpeg")},
            data={"country": "mozambique", "attested_dummy": "true"},
        )
        assert response.status_code == 202, response.text
        return response.json()["run_id"]

    def card_for(run_id: str) -> dict:
        with connect() as conn:
            return dict(
                conn.execute(
                    sql(
                        "select c.id, c.kind, c.image_path from cards c "
                        "join runs r on r.card_id = c.id where r.id = cast(:r as uuid)"
                    ),
                    {"r": run_id},
                ).mappings().one()
            )

    first_card = card_for(upload("first.jpg"))
    second_card = card_for(upload("second.jpg"))

    assert second_card["id"] == first_card["id"], "same bytes must not create a second card"
    assert second_card["image_path"] == first_card["image_path"]
    assert second_card["kind"] == "upload"
