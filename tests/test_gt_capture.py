"""Tests for the ground-truth capture tool.

The tool writes the files accuracy is measured against, so the write path is
tested rather than trusted. Everything runs against a temporary copy of a sheet.
"""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools/gt_capture"))

import sheet_io  # noqa: E402

REAL_SHEETS = ROOT / "countries/mozambique/ground_truth"


def _blank_sheet(form_id: str) -> dict:
    """A sheet with nothing filled in. Tests must not depend on the owner's data,
    which changes every time a card is read."""
    concepts = sheet_io.load_concepts()
    ids = sheet_io.scored_ids()
    return {
        "form_id": form_id,
        "image": f"{form_id}.jpg",
        "template": "template.jpg",
        "status": "pending",
        "fields_verified": 0,
        "fields_total": len(ids),
        "fields_scoreable": len(sheet_io.scoreable_ids(concepts)),
        "last_updated": "2026-10-23",
        "reviewed_by": None,
        "note": None,
        "fields": {cid: {"value": None, "status": "pending", "source": None} for cid in ids},
    }


@pytest.fixture()
def sheets(tmp_path, monkeypatch):
    for form_id in ("form19", "form30"):
        (tmp_path / f"{form_id}.json").write_text(json.dumps(_blank_sheet(form_id)))
    monkeypatch.setattr(sheet_io, "GT_DIR", tmp_path)
    return tmp_path


def test_groups_cover_every_sheet_field_exactly_once(sheets):
    ids = sheet_io.scored_ids()
    assert len(ids) == len(set(ids)), "a concept is grouped twice"
    sheet = sheet_io.load_sheet("form19")
    assert set(ids) == set(sheet["fields"]), "groups and sheet disagree"


def test_widget_follows_the_concept_not_the_name():
    concepts = sheet_io.load_concepts()
    assert sheet_io.widget_for(concepts["nerve_pain_cubital_left"]) == "radio"
    assert sheet_io.widget_for(concepts["classification"]) == "select"
    assert sheet_io.widget_for(concepts["disability_grade_hand_left"]) == "number"
    assert sheet_io.widget_for(concepts["date_diagnosed"]) == "date"
    assert sheet_io.widget_for(concepts["complications_hands"]) == "textarea"
    assert sheet_io.widget_for(concepts["patient_name"]) == "text"


def test_save_marks_values_verified_and_counts_them(sheets):
    sheet, problems = sheet_io.save_sheet(
        "form30",
        {"skin_patch_count": "3", "nerve_pain_cubital_left": "+", "itching": ""},
        reviewer="sam",
    )
    assert problems == []
    assert sheet["fields"]["skin_patch_count"]["status"] == "verified"
    assert sheet["fields"]["nerve_pain_cubital_left"]["value"] == "+"
    assert sheet["fields"]["itching"]["status"] == "pending"
    assert sheet["fields_verified"] == 2
    assert sheet["reviewed_by"] == "sam"
    assert sheet["status"] == "partial"
    disk = json.loads((sheets / "form30.json").read_text())
    assert disk["fields"]["skin_patch_count"]["value"] == "3"


def test_invalid_value_is_rejected_and_nothing_is_written(sheets):
    before = (sheets / "form30.json").read_text()
    sheet, problems = sheet_io.save_sheet("form30", {"disability_grade_hand_left": "3"})
    assert problems and "not one of" in problems[0]
    assert (sheets / "form30.json").read_text() == before


def test_non_numeric_count_is_rejected(sheets):
    _, problems = sheet_io.save_sheet("form30", {"skin_patch_count": "four"})
    assert problems and "whole number" in problems[0]


def test_bad_date_is_rejected(sheets):
    _, problems = sheet_io.save_sheet("form30", {"date_diagnosed": "12/03/2026"})
    assert problems and "year" in problems[0]


def test_identifying_field_withheld_does_not_touch_the_sheet(sheets):
    concept = sheet_io.load_concepts()["patient_name"]
    assert concept["sensitivity"] == "identifying"
    before = json.loads((sheets / "form30.json").read_text())
    sheet, problems = sheet_io.save_sheet("form30", {"patient_name": "Test Person"})
    assert problems == []
    assert sheet["fields"]["patient_name"] == before["fields"]["patient_name"]
    assert not (sheets / "_identifying" / "form30.json").exists()


def test_identifying_field_goes_to_the_sidecar_when_enabled(sheets):
    sheet, problems = sheet_io.save_sheet("form30", {"patient_name": "Test Person"}, include_identifying=True)
    assert problems == []
    assert sheet["fields"]["patient_name"]["status"] == "pending", "must never enter the committed sheet"
    sidecar = json.loads((sheets / "_identifying" / "form30.json").read_text())
    assert sidecar == {"patient_name": "Test Person"}


def test_clearing_a_verified_value_returns_it_to_pending(sheets):
    sheet_io.save_sheet("form19", {"skin_patch_count": "9"})
    sheet, _ = sheet_io.save_sheet("form19", {"skin_patch_count": ""})
    assert sheet["fields"]["skin_patch_count"]["status"] == "pending"
    assert sheet["fields"]["skin_patch_count"]["value"] is None


def test_insensitive_points_accept_the_full_range(sheets):
    for value in ("0", "3", "8"):
        sheet, problems = sheet_io.save_sheet("form30", {"insensitive_points_left_foot": value})
        assert problems == [], (value, problems)
        assert sheet["fields"]["insensitive_points_left_foot"]["value"] == value


def test_insensitive_points_reject_absurd_values(sheets):
    _, problems = sheet_io.save_sheet("form30", {"insensitive_points_left_foot": "88"})
    assert problems and "soft ceiling" in problems[0]

    _, problems = sheet_io.save_sheet("form30", {"insensitive_points_left_foot": "-1"})
    assert problems and "below the minimum" in problems[0]


def test_status_ignores_identifying_fields():
    concepts = sheet_io.load_concepts()
    ids = sheet_io.scoreable_ids(concepts)
    assert len(ids) == 48, "scoreable subset changed; six VLM-derived fields are excluded"
    fields = {cid: {"status": "verified", "value": "x"} for cid in ids}
    fields["patient_name"] = {"status": "pending", "value": None}
    status, verified, total = sheet_io.compute_status(fields, concepts)
    assert (status, verified, total) == ("verified", 48, 48)


def test_status_is_pending_when_nothing_is_read():
    concepts = sheet_io.load_concepts()
    status, verified, total = sheet_io.compute_status({}, concepts)
    assert (status, verified, total) == ("pending", 0, 48)
