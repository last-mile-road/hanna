"""The benchmark loader must read the new sheets and score only verified fields.

All sheets here are synthetic. The real ones carry the owner's readings, which
change whenever a card is reviewed, and a test that breaks on new data teaches
nothing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))

from benchmark_harness import GroundTruth, score  # noqa: E402


def _sheet(tmp_path: Path, verified: dict, name: str = "synthetic") -> Path:
    fields = {
        key: {"value": value, "status": "verified", "source": "test"}
        for key, value in verified.items()
    }
    fields["patient_name"] = {"value": None, "status": "pending", "source": None}
    path = tmp_path / f"{name}.json"
    path.write_text(
        json.dumps({"form_id": name, "image": f"{name}.jpg", "fields": fields})
    )
    return path


def test_loader_reads_verified_fields_only(tmp_path):
    path = _sheet(
        tmp_path,
        {"skin_patch_count": "0", "nerve_enlargement_radial_cutaneous_right": "+"},
    )
    gt = GroundTruth.from_json(path)
    assert gt.expected == {
        "skin_patch_count": "0",
        "nerve_enlargement_radial_cutaneous_right": "+",
    }
    assert "patient_name" not in gt.expected


def test_pending_sheet_scores_nothing(tmp_path):
    assert GroundTruth.from_json(_sheet(tmp_path, {})).expected == {}


def test_scoring_against_the_sheet(tmp_path):
    gt = GroundTruth.from_json(_sheet(tmp_path, {"skin_patch_count": "4", "gender": "M"}))
    predicted = dict(gt.expected)
    predicted["skin_patch_count"] = "3"  # one wrong count
    result = score(predicted, gt.expected, numeric_fields={"skin_patch_count"}, tol=0)
    assert result["skin_patch_count"] is False
    assert result["gender"] is True


def test_identifying_values_are_read_from_the_sidecar(tmp_path):
    plain = tmp_path / "plain"
    plain.mkdir()
    _sheet(plain, {"skin_patch_count": "4"})

    with_sidecar = tmp_path / "with_sidecar"
    (with_sidecar / "_identifying").mkdir(parents=True)
    _sheet(with_sidecar, {"skin_patch_count": "4"})
    (with_sidecar / "_identifying" / "synthetic.json").write_text(
        json.dumps({"patient_name": "Test Person", "contact_number": "123"})
    )

    plain_gt = GroundTruth.from_json(plain / "synthetic.json")
    side_gt = GroundTruth.from_json(with_sidecar / "synthetic.json")
    assert "patient_name" not in plain_gt.expected
    assert side_gt.expected["patient_name"] == "Test Person"
    assert side_gt.expected["contact_number"] == "123"
    assert len(side_gt.expected) == len(plain_gt.expected) + 2

