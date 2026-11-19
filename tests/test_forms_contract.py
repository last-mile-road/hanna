import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.forms import load_form_pack, validate


def test_mozambique_pack_loads():
    pack = load_form_pack("mozambique")
    assert pack.form["id"] == "mozambique_leprosy_card_pt"
    assert len(pack.blocks) == 8
    assert len(pack.pairs) == 66
    assert len(pack.concepts) >= 60


def test_every_pair_has_a_concept_and_unique_id():
    pack = load_form_pack("mozambique")
    ids = [pair.id for pair in pack.pairs]
    assert len(ids) == len(set(ids))
    assert all(pair.concept in pack.concepts for pair in pack.pairs)


def test_validation_rejects_unknown_concept():
    pack = load_form_pack("mozambique")
    pack.pairs[0].concept = "does_not_exist"
    errors = validate(pack)
    assert any("does_not_exist" in error for error in errors)


def test_validation_rejects_duplicate_block():
    pack = load_form_pack("mozambique")
    pack.blocks.append(pack.blocks[0])
    errors = validate(pack)
    assert any("duplicate block id" in error for error in errors)


def test_treatment_block_is_a_dose_grid():
    pack = load_form_pack("mozambique")
    block = next(b for b in pack.blocks if b.id == "treatment_follow")
    assert block.block_type == "dose_grid"
    assert "treatment_grid" in {p.id for p in block.pairs}
    derived = {p.id for p in block.pairs if p.derived}
    assert derived == {
        "treatment_start_date",
        "total_treatment_times",
        "treatment_months_total",
        "treatment_missed",
    }


def test_block_hints_are_loaded_and_used():
    from core.prompts import build_prompt

    pack = load_form_pack("mozambique")
    hand = next(b for b in pack.blocks if b.id == "hand_feet_assessment")
    clinical = next(b for b in pack.blocks if b.id == "clinical_history")
    assert hand.hint and "Count only X marks" in hand.hint
    assert clinical.hint and "six rows" in clinical.hint
    hand_prompt = build_prompt(hand, hand.pairs, pack.concepts, country="Mozambique", language="Portuguese")
    clinical_prompt = build_prompt(clinical, clinical.pairs, pack.concepts, country="Mozambique", language="Portuguese")
    assert "Count only X marks" in hand_prompt
    assert "six rows" in clinical_prompt


def test_back_pack_v3_has_the_front_parity_structure():
    pack = load_form_pack("mozambique", "form.back.yaml")
    assert len(pack.blocks) == 20
    assert len(pack.pairs) == 167
    ids = {block.id for block in pack.blocks}
    assert "visit_index" in ids
    assert "visit1_nerve" in ids and "visit1_sensory" in ids
    assert "visit1_def_exam" in ids
    assert "visit1_eye" not in ids and "visit1_muscle" not in ids
    assert "visit1_disability" not in ids
    def_exam = next(block for block in pack.blocks if block.id == "visit1_def_exam")
    assert def_exam.block_type == "annotated_table"
    assert len(def_exam.pairs) == 14
    assert "outcome_choice" in ids
    outcome = next(block for block in pack.blocks if block.id == "outcome_choice")
    # One final outcome, one pair: the four printed boxes are options on a
    # single dropdown, not four booleans to score separately.
    assert outcome.block_type == "value_pairs"
    assert [pair.id for pair in outcome.pairs] == ["outcome_decision"]
    prednisolone = next(block for block in pack.blocks if block.id == "prednisolone")
    assert [pair.id for pair in prednisolone.pairs] == [
        "prednisolone_status", "prednisolone_weeks_recorded", "prednisolone_table",
    ]
    reactions = next(block for block in pack.blocks if block.id == "reactions_exam")
    derived = [pair.id for pair in reactions.pairs if pair.derived]
    assert derived == ["reaction_diagnosed"]
    sensory = next(block for block in pack.blocks if block.id == "visit1_sensory")
    assert [pair.id for pair in sensory.pairs] == [
        "v1_insensitive_points_right_hand",
        "v1_insensitive_points_left_hand",
        "v1_insensitive_points_right_foot",
        "v1_insensitive_points_left_foot",
        "v1_complication_ulcer",
        "v1_complication_amputation",
        "v1_complication_contracture",
        "v1_complication_other",
        "v1_hand_foot_exam_notes",
    ]
