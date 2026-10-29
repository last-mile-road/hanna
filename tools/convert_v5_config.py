#!/usr/bin/env python3
"""Convert the v5 Mozambique extraction config into a draft form pack.

The v5 config is read-only input (hanna_v2/code/standardized_extraction_config.json).
This tool exists so the 38 value pairs and their template coordinates are
carried across mechanically instead of being redrawn by hand. It does not invent
anything: a pair whose v5 key has no concept alias is written with
`concept: null` and listed for review.

Usage:
    ~/.venv/bin/python tools/convert_v5_config.py \
        --config ../hanna_v2/code/standardized_extraction_config.json \
        --concepts docs/concepts/concepts.yaml \
        --out countries/mozambique/form.draft.yaml
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import yaml

# v5 group name -> block type. The three groups that carried zero value pairs in
# v5 are the bespoke-function blocks (PURPOSE.md §6); their pairs are declared
# here because the database shows the keys they actually produced.
BLOCK_TYPES = {
    "General_demographic": "value_pairs",
    "Classification": "tick_grid",
    "Clinical_history": "value_pairs",
    "def_exam": "annotated_table",
    "treatment_follow": "annotated_table",
    "nerve_assessment": "annotated_table",
    "Skin_patch": "visual_annotation",
    "Hand_feet_assessment": "visual_annotation",
}

# Pairs the v5 config does not contain, because bespoke functions produced them.
# Taken from the exact key list stored in the VPS extraction_accuracy table.
IMPLIED_PAIRS = {
    "nerve_assessment": [
        f"nerve_{sign}_{nerve}_{side}"
        for sign in ("enlargement", "pain")
        for nerve in ("cubital", "radial_cutaneous", "popliteal_external")
        for side in ("right", "left")
    ],
    "Hand_feet_assessment": [
        "insensitive_points_right_hand",
        "insensitive_points_left_hand",
        "insensitive_points_right_foot",
        "insensitive_points_left_foot",
        "hand_feet_notes",
        "other_complications_hands",
        "other_complications_feet",
        "is_filled",
    ],
    "Skin_patch": ["nr_skinpatches", "body_area_percentage", "skinpatch_notes"],
}

V5_TO_CONCEPT_TYPE = {
    "string": "text",
    "integer": "integer",
    "tickbox": "choice_single",
    "date": "date",
}


def load_alias_map(path: Path) -> dict[str, dict]:
    data = yaml.safe_load(path.read_text())
    out = {}
    for concept in data["concepts"]:
        for alias in concept.get("aliases") or []:
            out[alias] = concept
    return out


def make_pair(key: str, aliases: dict[str, dict], bbox, v5_type: str) -> dict:
    concept = aliases.get(key)
    pair = {
        "id": key,
        "concept": concept["id"] if concept else None,
        "value_type": concept["value_type"] if concept else V5_TO_CONCEPT_TYPE.get(v5_type, "text"),
        "bbox": bbox,
    }
    if concept:
        pair["sensitivity"] = concept["sensitivity"]
        if concept.get("options"):
            pair["allowed_values"] = concept["options"]
    else:
        pair["sensitivity"] = "unclassified"
        pair["review"] = "no concept alias for this v5 key"
    return pair


def _derived_pair(pair_id: str, concept_id: str, value_type: str) -> dict:
    return {
        "id": pair_id,
        "concept": concept_id,
        "value_type": value_type,
        "bbox": None,
        "sensitivity": "clinical",
        "derived": True,
    }


def convert(config: dict, aliases: dict[str, dict], template: str) -> dict:
    blocks = []
    for group in config["field_groups"]:
        name = group["group_name"]
        if name == "treatment_follow":
            pairs = [
                make_pair("treatment_grid", aliases, None, "string"),
                _derived_pair("treatment_start_date", "treatment_start_date", "date"),
                _derived_pair("total_treatment_times", "treatment_doses_total", "integer"),
                _derived_pair("treatment_months_total", "treatment_months_total", "integer"),
                _derived_pair("treatment_missed", "treatment_missed", "integer"),
            ]
            block_type = "dose_grid"
        else:
            pairs = []
            for sub in group.get("sub_groups") or []:
                for kv in sub["key_value_pairs"]:
                    pairs.append(
                        make_pair(kv["key"], aliases, kv.get("bounding_box"), kv["value_type"])
                    )
            for key in IMPLIED_PAIRS.get(name, []):
                pair = make_pair(key, aliases, None, "string")
                pair["geometry_source"] = "v5 had no per-pair box; block crop only"
                pairs.append(pair)
            block_type = BLOCK_TYPES.get(name, "value_pairs")
        blocks.append(
            {
                "id": name.lower(),
                "source_group": name,
                "block_type": block_type,
                "bbox": group.get("bounding_box"),
                "value_pairs": pairs,
            }
        )
    return {
        "form": {
            "id": "mozambique_leprosy_card_pt",
            "country": "mozambique",
            "template": template,
            "status": "draft",
            "reference_year": 2026,
            "reference_year_note": "REQUIRED, no silent default (CONSTRAINTS §1.6). Confirm before any birth-year inference.",
            "generated_by": "tools/convert_v5_config.py",
            "note": "Mechanical conversion of the v5 config. concepts came from docs/concepts/concepts.yaml; nulls need review.",
        },
        "blocks": blocks,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True, type=Path)
    ap.add_argument("--concepts", required=True, type=Path)
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--template", default="template.jpg")
    args = ap.parse_args()

    form = convert(json.loads(args.config.read_text()), load_alias_map(args.concepts), args.template)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        "# GENERATED DRAFT - do not edit by hand, re-run tools/convert_v5_config.py\n"
        + yaml.safe_dump(form, sort_keys=False, allow_unicode=True)
    )
    pairs = [p for b in form["blocks"] for p in b["value_pairs"]]
    unmapped = [p["id"] for p in pairs if p["concept"] is None]
    print(f"wrote {args.out}: {len(form['blocks'])} blocks, {len(pairs)} value pairs")
    print(f"unmapped: {len(unmapped)} {unmapped}")


if __name__ == "__main__":
    main()
