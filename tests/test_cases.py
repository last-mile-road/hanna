"""The case foundation: schema shape, and a round trip on the dev database.

The database tests run inside one transaction and roll back, so they leave no
rows behind. They are skipped when the dev Postgres is not running.
"""

from __future__ import annotations

from datetime import date

import pytest
from sqlalchemy import CheckConstraint, UniqueConstraint, text

from core import cases, store
from core.caseid import is_valid
from core.db import connect
from core.schema import metadata


def test_case_tables_are_declared():
    for name in (
        "cases",
        "captures",
        "capture_pages",
        "visits",
        "section_states",
        "case_values",
    ):
        assert name in metadata.tables


def test_hanna_number_is_unique_per_country_and_checked():
    table = metadata.tables["cases"]
    uniques = {c.name for c in table.constraints if isinstance(c, UniqueConstraint)}
    checks = {c.name for c in table.constraints if isinstance(c, CheckConstraint)}
    assert "uq_cases_country_hanna" in uniques
    assert "ck_cases_hanna_number" in checks


def test_section_state_has_case_and_visit_uniqueness():
    table = metadata.tables["section_states"]
    indexes = {index.name: index for index in table.indexes}
    assert indexes["uq_section_states_case_section"].unique
    assert indexes["uq_section_states_case_visit_section"].unique


def test_case_values_have_case_and_visit_uniqueness():
    table = metadata.tables["case_values"]
    indexes = {index.name: index for index in table.indexes}
    assert indexes["uq_case_values_case_concept"].unique
    assert indexes["uq_case_values_case_visit_concept"].unique


def _db_available() -> bool:
    try:
        with connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


@pytest.mark.skipif(not _db_available(), reason="dev database not running")
def test_case_capture_page_visit_and_confirmed_values_round_trip():
    with connect() as conn:
        try:
            country_id = store.upsert_country(conn, "zz_case_test", "Case test")

            case = cases.create_case(
                conn,
                country_id,
                government_id="NIL-TEST-1",
                identity_status="new",
                classification="MB",
                treatment_start_date=date(2026, 1, 10),
            )
            assert case["hanna_number"] and is_valid(case["hanna_number"])
            assert cases.find_case(conn, hanna_number=case["hanna_number"])["id"] == case["id"]
            assert cases.find_case(
                conn, country_id=country_id, government_id="NIL-TEST-1"
            )["id"] == case["id"]

            capture = cases.add_capture(
                conn,
                case["id"],
                source="camera",
                client={"device": "test-phone", "effective_type": "3g"},
            )
            front = cases.add_page(
                conn,
                capture["id"],
                page_no=1,
                shot_no=1,
                image_path="/tmp/front.jpg",
                md5="a" * 32,
                variant="front",
                admission="good",
                quality={"blur": 0.1, "glare": 0.0},
                match_inliers=120,
            )
            back = cases.add_page(
                conn,
                capture["id"],
                page_no=2,
                shot_no=2,
                image_path="/tmp/back.jpg",
                md5="b" * 32,
                variant="back",
                admission="good",
            )
            assert front["page_no"] == 1

            visit1 = cases.upsert_visit(
                conn,
                case["id"],
                visit_seq=1,
                visit_date=date(2026, 3, 14),
                visit_label="3 meses",
                capture_id=capture["id"],
                source_page_id=back["id"],
            )
            visit2 = cases.upsert_visit(
                conn,
                case["id"],
                visit_seq=2,
                visit_date=None,
                visit_label="6 meses",
                date_status="missing",
            )
            assert visit1["visit_date"] == date(2026, 3, 14)
            assert visit2["date_status"] == "missing"

            cases.set_section_state(
                conn,
                case["id"],
                "clinical_history",
                state="closed",
                capture_id=capture["id"],
                confirmed_by="tester",
            )
            cases.put_case_value(
                conn,
                case["id"],
                "classification",
                "MB",
                source_capture_id=capture["id"],
                source_page_id=front["id"],
                confirmed_by="tester",
            )
            cases.put_case_value(
                conn,
                case["id"],
                "leprosy_contact",
                None,  # confirmed blank
                source_capture_id=capture["id"],
                source_page_id=front["id"],
                confirmed_by="tester",
            )
            values = cases.confirmed_values(conn, case["id"])
            assert values["classification"] == "MB"
            assert values["leprosy_contact"] is None
            assert (None, "clinical_history") in cases.closed_sections(conn, case["id"])

            cases.set_section_state(
                conn,
                case["id"],
                "nerve",
                state="closed",
                visit_id=visit1["id"],
                capture_id=capture["id"],
                confirmed_by="tester",
            )
            cases.put_case_value(
                conn,
                case["id"],
                "nerve_enlargement_cubital_right",
                "Yes",
                visit_id=visit1["id"],
                source_capture_id=capture["id"],
                source_page_id=back["id"],
                confirmed_by="tester",
            )
            assert cases.confirmed_values(conn, case["id"], visit_id=visit1["id"]) == {
                "nerve_enlargement_cubital_right": "Yes"
            }
            assert (visit1["id"], "nerve") in cases.closed_sections(conn, case["id"])

            reopened = cases.set_section_state(
                conn,
                case["id"],
                "clinical_history",
                state="open",
                reopened_by="supervisor",
                note="clinician corrected the card",
            )
            assert reopened["state"] == "open"
            assert (None, "clinical_history") not in cases.closed_sections(
                conn, case["id"]
            )
        finally:
            conn.rollback()
