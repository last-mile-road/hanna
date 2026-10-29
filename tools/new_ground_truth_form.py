#!/usr/bin/env python3
"""Generate or refresh a ground-truth sheet for one card.

The sheet is the asset accuracy is measured against, so it is generated from
two inputs only: the concept index (what fields exist) and the evidence ledger
(what a human has stated). Nothing here is inferred from a model run.

Usage:
    ~/.venv/bin/python tools/new_ground_truth_form.py --form form16
    ~/.venv/bin/python tools/new_ground_truth_form.py --all

Re-running is safe: values marked `verified` in an existing sheet are kept, and
pending fields stay pending even if the ledger changes.
"""

from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
CONCEPTS = ROOT / "docs/concepts/concepts.yaml"
EVIDENCE = ROOT / "countries/mozambique/ground_truth/_evidence.yaml"
OUT_DIR = ROOT / "countries/mozambique/ground_truth"

# Order matters: the sheet is read by a human, so it follows the card.
SECTION_ORDER = [
    "patient_name", "patient_id", "previous_patient_id", "contact_number",
    "date_of_birth_or_age", "gender", "district", "locality", "village",
    "case_category", "retreatment_type", "transferred_from", "classification",
    "date_diagnosed", "mode_of_detection", "referred_by",
    "symptom_duration", "symptom_location", "itching", "leprosy_contact",
    "previous_leprosy_treatment", "other_registration_comments",
    "skin_patch_count", "body_area_percentage", "skin_lesion_notes",
    "nerve_enlargement_cubital_right", "nerve_enlargement_cubital_left",
    "nerve_enlargement_radial_cutaneous_right", "nerve_enlargement_radial_cutaneous_left",
    "nerve_enlargement_popliteal_external_right", "nerve_enlargement_popliteal_external_left",
    "nerve_pain_cubital_right", "nerve_pain_cubital_left",
    "nerve_pain_radial_cutaneous_right", "nerve_pain_radial_cutaneous_left",
    "nerve_pain_popliteal_external_right", "nerve_pain_popliteal_external_left",
    "eye_blinking_right", "eye_blinking_left",
    "eye_lagophthalmos_right", "eye_lagophthalmos_left",
    "hand_ulnar_right", "hand_ulnar_left",
    "foot_dorsiflex_right", "foot_dorsiflex_left",
    "insensitive_points_right_hand", "insensitive_points_left_hand",
    "insensitive_points_right_foot", "insensitive_points_left_foot",
    "hand_foot_exam_notes", "complications_hands", "complications_feet",
    "disability_grade_eye_right", "disability_grade_eye_left", "disability_grade_hand_right", "disability_grade_hand_left",
    "disability_grade_foot_right", "disability_grade_foot_left",
    "treatment_start_date", "treatment_doses_total", "treatment_months_total",
    "treatment_missed", "treatment_completion_date",
]


def ordered_concepts() -> list[dict]:
    data = yaml.safe_load(CONCEPTS.read_text())
    by_id = {c["id"]: c for c in data["concepts"]}
    scope_path = ROOT / "countries/mozambique/card_scope.yaml"
    absent = set()
    if scope_path.exists():
        absent = set((yaml.safe_load(scope_path.read_text()) or {}).get("absent_concepts") or {})
    missing = [
        c for c in by_id
        if c not in SECTION_ORDER
        and by_id[c].get("who_name")
        and by_id[c].get("ground_truth") is not False
        and c not in absent
    ]
    if missing:
        raise SystemExit(f"concept index has fields absent from SECTION_ORDER: {missing}")
    return [by_id[cid] for cid in SECTION_ORDER if cid in by_id and cid not in absent]


def build(form_id: str, entry: dict, concepts: list[dict], existing: dict | None) -> dict:
    seeds = entry.get("fields") or {}
    sources = {e["source"] for e in entry.get("evidence") or []}
    fields = {}
    verified = 0
    for concept in concepts:
        cid = concept["id"]
        prior = (existing or {}).get("fields", {}).get(cid, {})
        if prior.get("status") == "verified":
            fields[cid] = prior
            verified += 1
            continue
        if cid in seeds:
            fields[cid] = {
                "value": seeds[cid],
                "status": "verified",
                "source": "; ".join(sorted(sources)),
            }
            verified += 1
        else:
            fields[cid] = {"value": None, "status": "pending", "source": None}

    status = "pending" if verified == 0 else ("verified" if verified == len(fields) else "partial")
    # Completeness is judged over fields a committed sheet can hold: the eight
    # identifying fields never enter it, so counting them would mean no sheet
    # could ever finish. Same rule as the capture UI.
    scoreable = [c["id"] for c in concepts if c.get("sensitivity") != "identifying"]
    scoreable_verified = sum(1 for cid in scoreable if fields.get(cid, {}).get("status") == "verified")
    if scoreable_verified == 0:
        status = "pending"
    elif scoreable_verified == len(scoreable):
        status = "verified"
    else:
        status = "partial"

    return {
        "form_id": form_id,
        "image": entry["image"],
        "template": "template.jpg",
        "status": status,
        "fields_verified": verified,
        "fields_total": len(fields),
        "fields_scoreable": len(scoreable),
        "last_updated": date.today().isoformat(),
        "reviewed_by": (existing or {}).get("reviewed_by"),
        "note": entry.get("note"),
        "fields": fields,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--form")
    ap.add_argument("--all", action="store_true")
    args = ap.parse_args()

    evidence = yaml.safe_load(EVIDENCE.read_text())["forms"]
    concepts = ordered_concepts()
    targets = list(evidence) if args.all else [args.form]
    if not args.all and not args.form:
        raise SystemExit("pass --form <id> or --all")

    for form_id in targets:
        if form_id not in evidence:
            raise SystemExit(f"{form_id} is not in _evidence.yaml")
        out = OUT_DIR / f"{form_id}.json"
        existing = json.loads(out.read_text()) if out.exists() else None
        sheet = build(form_id, evidence[form_id], concepts, existing)
        out.write_text(json.dumps(sheet, indent=2) + "\n")
        print(f"{out.name}: {sheet['status']}, {sheet['fields_verified']}/{sheet['fields_total']} verified")


if __name__ == "__main__":
    main()
