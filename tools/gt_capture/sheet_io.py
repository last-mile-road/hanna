"""Reading and writing ground-truth sheets. No Streamlit, no web framework.

This module is deliberately separate from anything the pipeline will use. It
exists so the capture UI has testable logic instead of doing file writes inside
a page handler.

Two rules it enforces, because both are easy to get wrong by hand:

1. A value is written only if it is legal for that concept. A tick box gets
   + or -, an EHF field gets 0-2, and a typo is rejected rather than stored.
2. Identifying values never enter the committed sheet. They go to
   `_identifying/<form>.json`, which is gitignored. The sheet keeps those fields
   pending, so the repo stays clean and the accuracy number describes clinical
   fields rather than patient names.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]  # hanna_server/
GT_DIR = Path(os.environ.get("HANNA_GT_DIR", ROOT / "countries/mozambique/ground_truth"))
CONCEPTS_PATH = Path(os.environ.get("HANNA_CONCEPTS", ROOT / "docs/concepts/concepts.yaml"))
SCOPE_PATH = Path(os.environ.get("HANNA_SCOPE", ROOT / "countries/mozambique/card_scope.yaml"))
IMAGES_DIR = Path(
    os.environ.get(
        "HANNA_IMAGES_DIR",
        "/home/sam/Documents/hanna_project/hanna_archive/versions/v4_hanna3_2025-2026/test_forms",
    )
)
FORMS = ["form16", "form19", "form21", "form22", "form23", "form25", "form30"]

# Card order, grouped the way a reader works down the form. Every concept that
# appears in a sheet must appear here exactly once; a test enforces that.
GROUPS = {
    "Identity": [
        "patient_name", "patient_id", "previous_patient_id", "contact_number",
        "date_of_birth_or_age", "gender", "district", "locality", "village",
    ],
    "Registration": [
        "case_category", "retreatment_type", "transferred_from", "classification",
        "date_diagnosed", "mode_of_detection",
    ],
    "Clinical history": [
        "referred_by", "symptom_duration", "symptom_location", "itching",
        "leprosy_contact", "previous_leprosy_treatment", "other_registration_comments",
    ],
    "Skin patches": ["skin_patch_count"],
    "Nerves": [
        "nerve_enlargement_cubital_right", "nerve_enlargement_cubital_left",
        "nerve_enlargement_radial_cutaneous_right", "nerve_enlargement_radial_cutaneous_left",
        "nerve_enlargement_popliteal_external_right", "nerve_enlargement_popliteal_external_left",
        "nerve_pain_cubital_right", "nerve_pain_cubital_left",
        "nerve_pain_radial_cutaneous_right", "nerve_pain_radial_cutaneous_left",
        "nerve_pain_popliteal_external_right", "nerve_pain_popliteal_external_left",
    ],
    "Eye, hand and foot exam": [
        "eye_blinking_right", "eye_blinking_left",
        "eye_lagophthalmos_right", "eye_lagophthalmos_left",
        "hand_ulnar_right", "hand_ulnar_left",
        "foot_dorsiflex_right", "foot_dorsiflex_left",
    ],
    "Sensation and notes": [
        "insensitive_points_right_hand", "insensitive_points_left_hand",
        "insensitive_points_right_foot", "insensitive_points_left_foot",
        "complications_hands", "complications_feet",
    ],
    # The card's score table sits under the muscle test and sensation rows.
    "WHO disability grade (EHF)": [
        "disability_grade_eye_right", "disability_grade_eye_left",
        "disability_grade_hand_right", "disability_grade_hand_left",
        "disability_grade_foot_right", "disability_grade_foot_left",
    ],
    "Treatment": [
        "treatment_start_date", "treatment_completion_date",
    ],
}

DATE_RE = re.compile(r"^\d{4}(-\d{2}(-\d{2})?)?$")


def load_concepts() -> dict[str, dict]:
    data = yaml.safe_load(CONCEPTS_PATH.read_text())
    return {c["id"]: c for c in data["concepts"] if c.get("ground_truth") is not False}


def load_absent() -> set[str]:
    """Concepts this card does not print, from card_scope.yaml."""
    if not SCOPE_PATH.exists():
        return set()
    data = yaml.safe_load(SCOPE_PATH.read_text()) or {}
    return set((data.get("absent_concepts") or {}).keys())


def scored_ids() -> list[str]:
    absent = load_absent()
    concepts = load_concepts()
    return [
        cid
        for ids in GROUPS.values()
        for cid in ids
        if cid not in absent and cid in concepts
    ]


def widget_for(concept: dict) -> str:
    """Which control to show. Chosen from the concept, never from the key name."""
    value_type = concept.get("value_type")
    if value_type == "tick_box":
        return "radio"
    if value_type == "choice_single":
        return "select"
    if value_type in {"integer", "percent"}:
        return "number"
    if value_type == "date":
        return "date"
    cid = concept["id"]
    if cid.endswith("_notes") or cid.startswith("complications_"):
        return "textarea"
    return "text"


def allowed_values(concept: dict) -> list[str]:
    """What this card can actually say.

    `observed` is the card's vocabulary (M/F, new_case); `options` is the WHO
    vocabulary. When both exist the card wins, because a reader can only verify
    what is printed. Canonicalising to WHO terms is a separate step, and mixing
    the two is the bug this function exists to prevent.
    """
    source = concept.get("observed") or concept.get("options") or []
    return [str(o) for o in source]


def load_sheet(form_id: str) -> dict:
    path = GT_DIR / f"{form_id}.json"
    if not path.exists():
        raise FileNotFoundError(f"no sheet for {form_id}")
    return json.loads(path.read_text())


def scoreable_ids(concepts: dict[str, dict]) -> list[str]:
    """Fields a committed sheet can hold. Identifying ones are excluded, so
    completeness has to be measured without them or a sheet can never finish."""
    return [cid for cid in scored_ids() if concepts[cid].get("sensitivity") != "identifying"]


def compute_status(fields: dict, concepts: dict[str, dict]) -> tuple[str, int, int]:
    ids = scoreable_ids(concepts)
    verified = sum(1 for cid in ids if fields.get(cid, {}).get("status") == "verified")
    status = "pending" if verified == 0 else ("verified" if verified == len(ids) else "partial")
    return status, verified, len(ids)


def load_identifying(form_id: str) -> dict:
    path = GT_DIR / "_identifying" / f"{form_id}.json"
    return json.loads(path.read_text()) if path.exists() else {}


def validate(values: dict, concepts: dict[str, dict]) -> list[str]:
    problems = []
    for cid, raw in values.items():
        if cid not in concepts:
            problems.append(f"{cid}: not in the concept index")
            continue
        value = str(raw or "").strip()
        if value == "":
            continue
        concept = concepts[cid]
        options = allowed_values(concept)
        if options and value not in options:
            problems.append(f"{cid}: '{value}' is not one of {options}")
        if concept.get("value_type") in {"integer", "percent"} and not re.fullmatch(r"-?\d+", value):
            problems.append(f"{cid}: '{value}' is not a whole number")
        minimum = concept.get("min")
        if minimum is not None and re.fullmatch(r"-?\d+", value) and int(value) < minimum:
            problems.append(f"{cid}: '{value}' is below the minimum of {minimum}")
        ceiling = concept.get("sanity_max")
        if ceiling is not None and re.fullmatch(r"-?\d+", value) and int(value) > ceiling:
            problems.append(
                f"{cid}: '{value}' looks too high (soft ceiling {ceiling}); "
                f"confirm, and raise sanity_max on the concept if this country tests more"
            )
        if concept.get("value_type") == "date" and not DATE_RE.fullmatch(value):
            problems.append(f"{cid}: '{value}' is not a year, year-month or full date")
    return problems


def save_sheet(
    form_id: str,
    values: dict,
    reviewer: str = "",
    include_identifying: bool = False,
) -> tuple[dict, list[str]]:
    """Merge submitted values into the sheet. Returns (sheet, problems).

    Nothing is written when a value is invalid. Blank stays pending, which is the
    only honest representation of "I could not read this".
    """
    concepts = load_concepts()
    problems = validate(values, concepts)
    if problems:
        return load_sheet(form_id), problems

    sheet = load_sheet(form_id)
    today = date.today().isoformat()
    identifying: dict[str, str] = {}

    for cid, raw in values.items():
        concept = concepts[cid]
        value = str(raw or "").strip()
        if concept.get("sensitivity") == "identifying":
            if not include_identifying:
                # Leave the sheet untouched. Overwriting a previously verified
                # value with "pending" because the toggle was off would lose data.
                continue
            if value:
                identifying[cid] = value
            continue
        sheet["fields"][cid] = {
            "value": value or None,
            "status": "verified" if value else "pending",
            "source": f"owner reading, {today}" if value else None,
        }

    if identifying:
        ident_dir = GT_DIR / "_identifying"
        ident_dir.mkdir(exist_ok=True)
        _atomic_write(ident_dir / f"{form_id}.json", json.dumps(identifying, indent=2) + "\n")

    if reviewer:
        sheet["reviewed_by"] = reviewer
    verified = sum(1 for f in sheet["fields"].values() if f["status"] == "verified")
    status, scoreable_verified, scoreable_total = compute_status(sheet["fields"], concepts)
    sheet["fields_verified"] = verified
    sheet["fields_total"] = len(sheet["fields"])
    sheet["fields_scoreable"] = scoreable_total
    sheet["last_updated"] = today
    sheet["status"] = status

    _atomic_write(GT_DIR / f"{form_id}.json", json.dumps(sheet, indent=2) + "\n")
    return sheet, []


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    tmp.replace(path)

