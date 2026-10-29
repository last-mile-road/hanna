"""Checks on the concept index, the ground-truth sheets and the template.

These are the assets accuracy depends on, so they are the assets that get a test.
Pure functions, no model calls, no network.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
CONCEPTS = yaml.safe_load((ROOT / "docs/concepts/concepts.yaml").read_text())["concepts"]
GT_DIR = ROOT / "countries/mozambique/ground_truth"
FORMS = ["form16", "form19", "form21", "form22", "form23", "form25", "form30"]
TEMPLATE_MD5 = "92e2c6befb2c63e8403a83edeb70075f"


def test_concept_ids_and_aliases_are_unique():
    ids = [c["id"] for c in CONCEPTS]
    aliases = [a for c in CONCEPTS for a in (c.get("aliases") or [])]
    assert len(ids) == len(set(ids)), "duplicate concept id"
    assert len(aliases) == len(set(aliases)), "alias used by two concepts"


def test_every_concept_declares_sensitivity_and_type():
    for c in CONCEPTS:
        assert c.get("value_type"), f"{c['id']} has no value_type"
        assert c.get("sensitivity") in {"identifying", "clinical", "administrative"}, c["id"]


@pytest.mark.parametrize("form_id", FORMS)
def test_sheet_matches_concept_index(form_id):
    sheet = json.loads((GT_DIR / f"{form_id}.json").read_text())
    scope = yaml.safe_load((ROOT / "countries/mozambique/card_scope.yaml").read_text())
    absent = set((scope.get("absent_concepts") or {}).keys())
    scored = {
        c["id"] for c in CONCEPTS
        if c.get("ground_truth") is not False and c["id"] not in absent
    }
    derived = {
        cid for cid, field in sheet["fields"].items() if field["status"] == "derived"
    }
    assert set(sheet["fields"]) - derived == scored, "sheet fields drifted from the concept index"
    by_id = {c["id"]: c for c in CONCEPTS}
    for cid in derived:
        assert by_id[cid].get("ground_truth") is False, f"{cid} is derived but scoreable"
    assert sheet["fields_verified"] == sum(
        1 for f in sheet["fields"].values() if f["status"] == "verified"
    )


@pytest.mark.parametrize("form_id", FORMS)
def test_verified_values_are_allowed(form_id):
    by_id = {c["id"]: c for c in CONCEPTS}
    sheet = json.loads((GT_DIR / f"{form_id}.json").read_text())
    for cid, field in sheet["fields"].items():
        if field["status"] == "derived":
            assert field["value"] is not None, f"{cid} is derived but has no value"
            continue
        if field["status"] != "verified":
            assert field["value"] is None, f"{cid} has a value but is not verified"
            continue
        assert field["source"], f"{cid} is verified with no named source"
        # A verified value may use the WHO label or the card's own wording. The
        # card writes "F"; WHO says "Female". Both are accepted here, and which
        # one scoring should expect is an open question recorded in
        # countries/mozambique/ground_truth/README.md.
        allowed = [str(o) for o in (by_id[cid].get("options") or [])]
        allowed += [str(o) for o in (by_id[cid].get("observed") or [])]
        if allowed:
            assert field["value"] in allowed, f"{cid}={field['value']} not in {allowed}"


def test_template_is_the_measured_scan():
    actual = hashlib.md5((ROOT / "countries/mozambique/template.jpg").read_bytes()).hexdigest()
    assert actual == TEMPLATE_MD5, (
        "template.jpg is not the scan the coordinates were measured on; "
        "see countries/mozambique/template.md"
    )


def test_sidecar_overrides_the_sheet_without_duplicating():
    """A concept present in both files must yield one row, sidecar wins."""
    import sys

    sys.path.insert(0, str(ROOT))
    from core.forms import load_form_pack
    from tools.load_pack import ground_truth_rows

    pack = load_form_pack("mozambique", "form.draft.yaml")
    rows, _, _ = ground_truth_rows(GT_DIR / "form16.json", pack)
    by_pair = {}
    for row in rows:
        assert row["pair_id"] not in by_pair, f"duplicate row for {row['pair_id']}"
        by_pair[row["pair_id"]] = row
    reference = [r for r in rows if r["concept_id"] == "reference_person"]
    assert len(reference) == 1
    assert reference[0]["source"] == "identifying sidecar" or "owner reading" in reference[0]["source"]
