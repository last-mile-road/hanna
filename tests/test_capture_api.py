"""The capture API and the binary admission gate, end to end.

The admission tests create real rows and uploads; each test removes what it made.
Skipped when the dev database is not running.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core.caseid import is_valid
from core.db import connect
from core.schema import metadata
from server.app import app

client = TestClient(app)

FRONT = Path(__file__).resolve().parents[1] / "countries/mozambique/template.jpg"
BACK = Path(__file__).resolve().parents[1] / "countries/mozambique/template.back.jpg"


def _db_available() -> bool:
    try:
        with connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


def _post(files, **data):
    return client.post(
        "/api/captures",
        files=[("files", (name, payload, "image/jpeg")) for name, payload in files],
        data={"country": "mozambique", "attested_dummy": "true", **data},
    )


def _front():
    return FRONT.read_bytes()


def _back():
    return BACK.read_bytes()


def _cleanup(case_id: str) -> None:
    with connect() as conn:
        paths = [
            row[0]
            for row in conn.execute(
                text(
                    "select p.image_path from capture_pages p "
                    "join captures c on c.id = p.capture_id "
                    "where c.case_id = cast(:id as uuid)"
                ),
                {"id": case_id},
            ).all()
        ]
        conn.execute(
            text("delete from cases where id = cast(:id as uuid)"), {"id": case_id}
        )
        conn.commit()
    for path in paths:
        Path(path).unlink(missing_ok=True)


def test_capture_tables_are_in_the_schema():
    assert "captures" in metadata.tables
    assert "capture_pages" in metadata.tables


def test_attestation_is_required():
    response = client.post(
        "/api/captures",
        files=[("files", ("front.jpg", _front(), "image/jpeg"))],
        data={"country": "mozambique", "attested_dummy": "false"},
    )
    assert response.status_code == 422
    assert "cartão de teste" in response.json()["detail"]


def test_invalid_hanna_number_is_rejected_before_alignment():
    response = _post([("front.jpg", _front())], hanna_number="123")
    assert response.status_code == 422


def test_unknown_hanna_number_is_404_before_alignment():
    response = _post([("front.jpg", _front())], hanna_number="H 799 273 982")
    assert response.status_code == 404


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_two_sides_are_admitted_and_the_case_is_created():
    response = _post([("front.jpg", _front()), ("back.jpg", _back())])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "admitted", body
    assert len(body["pages"]) == 2
    assert [page["side"] for page in body["pages"]] == ["front", "back"]
    assert all(page["admission"] == "good" for page in body["pages"])
    assert is_valid(body["hanna_number"])
    assert body["hanna_number_display"].startswith("H ")
    assert body["missing"] == [] and body["duplicates"] == []

    image = client.get(body["pages"][0]["image_url"])
    assert image.status_code == 200
    assert image.headers["content-type"].startswith("image/")

    try:
        with connect() as conn:
            count = conn.execute(
                text(
                    "select count(*) from capture_pages "
                    "where capture_id = cast(:id as uuid)"
                ),
                {"id": body["capture_id"]},
            ).scalar_one()
            assert count == 2
            case = conn.execute(
                text("select hanna_number, status from cases where id = cast(:id as uuid)"),
                {"id": body["case_id"]},
            ).mappings().one()
            assert case["hanna_number"] == body["hanna_number"]
            assert case["status"] == "open"
    finally:
        _cleanup(body["case_id"])


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_a_follow_up_with_the_written_number_uses_the_same_case():
    first = _post([("front.jpg", _front()), ("back.jpg", _back())])
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["status"] == "admitted"

    try:
        second = _post(
            [("front.jpg", _front()), ("back.jpg", _back())],
            hanna_number=first_body["hanna_number_display"],
        )
        assert second.status_code == 200, second.text
        second_body = second.json()
        assert second_body["status"] == "admitted"
        assert second_body["case_id"] == first_body["case_id"]
        assert second_body["matched_by"] == "hanna_number"
        assert second_body["capture_id"] != first_body["capture_id"]
    finally:
        _cleanup(first_body["case_id"])


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_a_front_only_capture_is_a_retake_and_creates_no_case():
    response = _post([("front.jpg", _front())])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "retake"
    assert body["missing"] == ["back"]
    assert "case_id" not in body
    assert all(page["admission"] == "retake" for page in body["pages"] if page["side"] == "back")


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_two_photos_of_the_front_are_a_duplicate_and_a_retake():
    response = _post([("front1.jpg", _front()), ("front2.jpg", _front())])
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "retake"
    assert body["duplicates"] == ["front"]
    assert body["missing"] == ["back"]
