"""Checks the v5 config conversion, which is how 61 value pairs and their
template coordinates enter the new form pack. Re-runnable and offline."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "tools"))

from convert_v5_config import convert, load_alias_map  # noqa: E402

V5_CONFIG = ROOT.parent / "hanna_v2/code/standardized_extraction_config.json"
CONCEPTS = ROOT / "docs/concepts/concepts.yaml"


@pytest.fixture(scope="module")
def form():
    import json

    return convert(
        json.loads(V5_CONFIG.read_text()),
        load_alias_map(CONCEPTS),
        "template.jpg",
    )


def test_block_and_pair_counts(form):
    assert len(form["blocks"]) == 8
    assert sum(len(b["value_pairs"]) for b in form["blocks"]) == 66


def test_every_pair_resolves_to_a_concept(form):
    unmapped = [p["id"] for b in form["blocks"] for p in b["value_pairs"] if p["concept"] is None]
    assert unmapped == []


def test_blocks_with_no_v5_pairs_are_declared_with_implied_pairs(form):
    by_id = {b["id"]: b for b in form["blocks"]}
    for block in ("nerve_assessment", "skin_patch", "hand_feet_assessment"):
        assert by_id[block]["value_pairs"], f"{block} lost its implied pairs"
    assert len(by_id["nerve_assessment"]["value_pairs"]) == 12


def test_reference_year_is_explicit(form):
    assert isinstance(form["form"]["reference_year"], int), "reference_year must never be implicit"


def test_committed_draft_is_not_stale(form, tmp_path):
    committed = yaml.safe_load(
        (ROOT / "countries/mozambique/form.draft.yaml").read_text().split("\n", 1)[1]
    )
    assert committed == form, "form.draft.yaml is out of date; re-run tools/convert_v5_config.py"
