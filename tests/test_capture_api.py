"""The capture API and the binary admission gate, end to end.

The admission tests create real rows and uploads; each test removes what it made.
Skipped when the dev database is not running.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from core import caseid
from core import cases as cases_mod
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
        data={
            "country": "mozambique",
            "attested_dummy": "true",
            "start_extraction": "false",
            **data,
        },
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
    response = _post([("front.jpg", _front())], hanna_number="799 273 982")
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
    assert body["hanna_number"] is None
    assert body["hanna_number_display"] is None
    assert body["number_allocated"] is False
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
            assert case["hanna_number"] is None
            assert case["status"] == "open"
    finally:
        _cleanup(body["case_id"])


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_a_follow_up_with_the_written_number_uses_the_same_case():
    first = _post([("front.jpg", _front()), ("back.jpg", _back())])
    assert first.status_code == 200, first.text
    first_body = first.json()
    assert first_body["status"] == "admitted"
    assert first_body["hanna_number"] is None

    try:
        with connect() as conn:
            allocated = cases_mod.allocate_number(
                conn, uuid.UUID(first_body["case_id"])
            )
            conn.commit()
        assert allocated["hanna_number"] and caseid.is_valid(allocated["hanna_number"])

        second = _post(
            [("front.jpg", _front()), ("back.jpg", _back())],
            hanna_number=caseid.format_number(allocated["hanna_number"]),
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


def _cleanup_with_runs(case_id: str, run_ids: list[str]) -> None:
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
        for run_id in run_ids:
            conn.execute(
                text("delete from jobs where payload->>'run_id' = :rid"),
                {"rid": run_id},
            )
            conn.execute(
                text("delete from runs where id = cast(:rid as uuid)"),
                {"rid": run_id},
            )
        for path in paths:
            conn.execute(
                text("delete from cards where image_path = :path"), {"path": path}
            )
        conn.execute(
            text("delete from cases where id = cast(:id as uuid)"), {"id": case_id}
        )
        conn.commit()
    for path in paths:
        Path(path).unlink(missing_ok=True)


def test_capture_page_is_served():
    response = client.get("/capture")
    assert response.status_code == 200
    assert "Frente" in response.text
    assert client.get("/static/capture.js").status_code == 200


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_admission_starts_one_extraction_run_per_side():
    response = _post(
        [("front.jpg", _front()), ("back.jpg", _back())],
        start_extraction="true",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "admitted"
    assert len(body["runs"]) == 2
    assert {run["side"] for run in body["runs"]} == {"front", "back"}
    assert all(page.get("run_id") for page in body["pages"])
    run_ids = [run["run_id"] for run in body["runs"]]
    try:
        with connect() as conn:
            for run in body["runs"]:
                row = conn.execute(
                    text("select status from runs where id = cast(:id as uuid)"),
                    {"id": run["run_id"]},
                ).mappings().one()
                assert row["status"] == "queued"
                jobs = conn.execute(
                    text("select count(*) from jobs where payload->>'run_id' = :id"),
                    {"id": run["run_id"]},
                ).scalar_one()
                assert jobs == 1
    finally:
        _cleanup_with_runs(body["case_id"], run_ids)


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_number_is_allocated_only_after_both_runs_are_clear():
    response = _post(
        [("front.jpg", _front()), ("back.jpg", _back())],
        start_extraction="true",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["hanna_number"] is None
    run_ids = [run["run_id"] for run in body["runs"]]
    try:
        blocked = client.post(f"/api/cases/{body['case_id']}/allocate")
        assert blocked.status_code == 409

        # Simulate the worker: both pages read with no blocking condition.
        with connect() as conn:
            for run in body["runs"]:
                conn.execute(
                    text(
                        "update runs set status='done', validation_status='ok' "
                        "where id = cast(:id as uuid)"
                    ),
                    {"id": run["run_id"]},
                )
            conn.commit()

        ok = client.post(f"/api/cases/{body['case_id']}/allocate")
        assert ok.status_code == 200, ok.text
        allocated = ok.json()
        assert allocated["allocated"] is True
        assert caseid.is_valid(allocated["hanna_number"])
        assert allocated["hanna_number_display"] == caseid.format_number(
            allocated["hanna_number"]
        )
    finally:
        _cleanup_with_runs(body["case_id"], run_ids)


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_reviewed_values_become_ground_truth():
    from core import store
    from core.schema import cards as cards_t

    with connect() as conn:
        country_id = conn.execute(text("select id from countries where code='mozambique'")).scalar_one()
        form = conn.execute(
            text(
                "select id, version from forms "
                "where coalesce(definition->>'variant','draft') = 'draft' "
                "order by version desc limit 1"
            )
        ).mappings().one()
        card_id = conn.execute(
            cards_t.insert()
            .values(
                country_id=country_id,
                kind="test",
                image_path="/home/sam/tmp/opencode/gt-test.jpg",
                md5="d" * 32,
                attested_dummy=True,
            )
            .returning(cards_t.c.id)
        ).scalar_one()
        run_id = store.create_run(
            conn, card_id, form["id"], form["version"], "or-qwen35-9b-v1", "test", status="done"
        )
        block_run_id = store.add_block_run(
            conn,
            {
                "run_id": run_id,
                "block_id": "classification",
                "block_type": "value_pairs",
                "profile_id": "or-qwen35-9b-v1",
                "status": "done",
            },
        )
        store.add_observations(
            conn,
            [
                {
                    "run_id": run_id,
                    "block_run_id": block_run_id,
                    "block_id": "classification",
                    "pair_id": "leprosy_classification",
                    "concept_id": "classification",
                    "value": "MB",
                    "review_mode": "confirmed",
                },
                {
                    "run_id": run_id,
                    "block_run_id": block_run_id,
                    "block_id": "classification",
                    "pair_id": "not_reviewed",
                    "concept_id": "classification",
                    "value": "PB",
                    "review_mode": "skipped",
                },
            ],
        )
        conn.commit()

    try:
        response = client.post(f"/api/runs/{run_id}/ground-truth")
        assert response.status_code == 200, response.text
        assert response.json()["saved"] == 1
        with connect() as conn:
            rows = conn.execute(
                text(
                    "select gt_key, value, status from ground_truth "
                    "where card_id = :card_id order by gt_key"
                ),
                {"card_id": card_id},
            ).mappings().all()
            assert [(row["gt_key"], row["value"]) for row in rows] == [
                ("leprosy_classification", "MB")
            ]
            assert rows[0]["status"] == "verified"
    finally:
        with connect() as conn:
            conn.execute(
                text("delete from ground_truth where card_id = :card_id"),
                {"card_id": card_id},
            )
            conn.execute(
                text("delete from observations where run_id = cast(:run_id as uuid)"),
                {"run_id": str(run_id)},
            )
            conn.execute(
                text("delete from block_runs where run_id = cast(:run_id as uuid)"),
                {"run_id": str(run_id)},
            )
            conn.execute(
                text("delete from runs where id = cast(:run_id as uuid)"),
                {"run_id": str(run_id)},
            )
            conn.execute(
                text("delete from cards where id = :card_id"), {"card_id": card_id}
            )
            conn.commit()


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_a_closed_card_records_its_outcome_before_the_number():
    response = _post(
        [("front.jpg", _front()), ("back.jpg", _back())],
        start_extraction="true",
    )
    assert response.status_code == 200, response.text
    body = response.json()
    run_ids = [run["run_id"] for run in body["runs"]]
    try:
        closed = client.post(
            f"/api/cases/{body['case_id']}/close",
            json={"outcome": "outcome_released", "outcome_date": "2026-11-09"},
        )
        assert closed.status_code == 200, closed.text
        assert closed.json()["status"] == "closed"
        with connect() as conn:
            case = conn.execute(
                text(
                    "select status, outcome, outcome_date from cases "
                    "where id = cast(:id as uuid)"
                ),
                {"id": body["case_id"]},
            ).mappings().one()
            assert case["status"] == "closed"
            assert case["outcome"] == "outcome_released"
            assert str(case["outcome_date"]) == "2026-11-09"
    finally:
        _cleanup_with_runs(body["case_id"], run_ids)
