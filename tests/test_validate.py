from datetime import date

from core.forms import load_form_pack
from core.validate import validate_run

AS_OF = date(2026, 10, 28)


def base_values():
    values = {
        "unique_nr": "KDR-55-7210",
        "patient_name": "Samuel Alberto Joao",
        "birth_year": "1990",
        "gender": "M",
        "district": "Balama",
        "locality": "Impiri",
        "patient_reference": "Chefe da Aldera",
        "village_name": "Impiri",
        "date_diagnosed": "2025-01-01",
        "admin_classification": "new_case",
        "leprosy_classification": "MB_adult",
        "skinpatch_itching": "No",
        "leprosy_contact": "No",
        "previous_leptreatment": "No",
        "who_referred": "self referred",
        "nr_skinpatches": "4",
        "is_filled": "True",
        "treatment_start_date": "2025-02-01",
        "total_treatment_times": "12",
        "treatment_missed": "0",
        "treatment_months_total": "12",
    }
    nerves = [
        "nerve_enlargement_cubital_right", "nerve_enlargement_cubital_left",
        "nerve_enlargement_radial_cutaneous_right", "nerve_enlargement_radial_cutaneous_left",
        "nerve_enlargement_popliteal_external_right", "nerve_enlargement_popliteal_external_left",
        "nerve_pain_cubital_right", "nerve_pain_cubital_left",
        "nerve_pain_radial_cutaneous_right", "nerve_pain_radial_cutaneous_left",
        "nerve_pain_popliteal_external_right", "nerve_pain_popliteal_external_left",
    ]
    for nerve in nerves:
        values[nerve] = "-"
    values["nerve_enlargement_cubital_right"] = "+"
    for side in ("right", "left"):
        values[f"ehf_{side}_eye"] = "0"
        values[f"ehf_{side}_hand"] = "0"
        values[f"ehf_{side}_foot"] = "0"
        values[f"{side}_eye_blinking"] = "Yes"
        values[f"{side}_eye_lagoft"] = "0"
        values[f"insensitive_points_{side}_hand"] = "0"
        values[f"insensitive_points_{side}_foot"] = "0"
    values["right_hipothenar"] = "Normal"
    values["left_hipothenar"] = "Normal"
    values["right_foot_dorsiflex"] = "Normal"
    values["left_foot_dorsiflex"] = "Normal"
    return values


def run(values):
    pack = load_form_pack()
    return validate_run(
        pack.blocks, pack.pairs, pack.concepts, values, as_of=AS_OF
    )


def test_valid_record_is_ok():
    report = run(base_values())
    assert report["status"] == "ok", report
    assert report["blocking"] == 0


def test_missing_patient_name_blocks_with_advice_and_bbox():
    values = base_values()
    values.pop("patient_name")
    report = run(values)
    issue = next(i for i in report["issues"] if i["pair_id"] == "patient_name")
    assert issue["severity"] == "blocking"
    assert issue["advice"] and "nome" in issue["advice"].lower()
    assert issue["bbox"] == [73, 397, 1457, 513]
    assert report["status"] == "blocked"


def test_multiple_gender_marks_block():
    values = base_values()
    values["gender"] = "M,F"
    report = run(values)
    assert any(i["pair_id"] == "gender" and i["kind"] == "multiple_options" for i in report["issues"])


def test_hand_feet_section_needs_a_mark():
    values = base_values()
    values["is_filled"] = "False"
    report = run(values)
    issue = next(i for i in report["issues"] if i["block_id"] == "hand_feet_assessment")
    assert issue["severity"] == "blocking"


def test_disability_grade_outside_scale_blocks():
    values = base_values()
    values["ehf_left_hand"] = "3"
    report = run(values)
    assert any(i["pair_id"] == "ehf_left_hand" and i["severity"] == "blocking" for i in report["issues"])


def test_treatment_start_before_diagnosis_blocks():
    values = base_values()
    values["treatment_start_date"] = "2024-12-01"
    report = run(values)
    assert any(i["pair_id"] == "treatment_start_date" and i["severity"] == "blocking" for i in report["issues"])


def test_no_cardinal_signs_is_information_only():
    values = base_values()
    values["nr_skinpatches"] = "0"
    for nerve in [k for k in values if k.startswith("nerve_enlargement_")]:
        values[nerve] = "-"
    report = run(values)
    assert any(i["kind"] == "cross_check" and i["severity"] == "info" for i in report["issues"])
    assert report["blocking"] == 0


def test_missing_nil_is_a_system_flag_not_a_block():
    values = base_values()
    values.pop("unique_nr")
    report = run(values)
    issue = next(i for i in report["issues"] if i["kind"] == "system_flag")
    assert issue["audience"] == ["system"]
    assert report["blocking"] == 0


def test_late_no_start_is_information():
    values = base_values()
    values.pop("treatment_start_date")
    values["total_treatment_times"] = "0"
    report = run(values)
    assert any(i["kind"] == "cross_check" and i["severity"] == "info" for i in report["issues"])
    assert report["blocking"] == 0


def test_grade_zero_with_abnormal_sign_is_information():
    values = base_values()
    values["insensitive_points_right_hand"] = "2"
    report = run(values)
    assert any(
        i["block_id"] == "def_exam" and i["severity"] == "info"
        and "não coincidem" in (i["advice"] or "")
        for i in report["issues"]
    )
    assert report["blocking"] == 0
