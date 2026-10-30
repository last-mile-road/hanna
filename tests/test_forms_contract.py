import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.forms import load_form_pack, validate


def test_mozambique_pack_loads():
    pack = load_form_pack()
    assert pack.form["id"] == "mozambique_leprosy_card_pt"
    assert len(pack.blocks) == 8
    assert len(pack.pairs) == 66
    assert len(pack.concepts) >= 60


def test_every_pair_has_a_concept_and_unique_id():
    pack = load_form_pack()
    ids = [pair.id for pair in pack.pairs]
    assert len(ids) == len(set(ids))
    assert all(pair.concept in pack.concepts for pair in pack.pairs)


def test_validation_rejects_unknown_concept():
    pack = load_form_pack()
    pack.pairs[0].concept = "does_not_exist"
    errors = validate(pack)
    assert any("does_not_exist" in error for error in errors)


def test_validation_rejects_duplicate_block():
    pack = load_form_pack()
    pack.blocks.append(pack.blocks[0])
    errors = validate(pack)
    assert any("duplicate block id" in error for error in errors)


def test_treatment_block_is_a_dose_grid():
    pack = load_form_pack()
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

    pack = load_form_pack()
    hand = next(b for b in pack.blocks if b.id == "hand_feet_assessment")
    clinical = next(b for b in pack.blocks if b.id == "clinical_history")
    assert hand.hint and "Count only X marks" in hand.hint
    assert clinical.hint and "six rows" in clinical.hint
    hand_prompt = build_prompt(hand, hand.pairs, pack.concepts)
    clinical_prompt = build_prompt(clinical, clinical.pairs, pack.concepts)
    assert "Count only X marks" in hand_prompt
    assert "six rows" in clinical_prompt
