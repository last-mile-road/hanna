"""The tick strip spec: seven groups, string options, one pair per group."""

from __future__ import annotations

from core import ticks


def test_seven_groups_cover_the_tick_fields():
    groups = ticks.groups(ticks.load_spec())
    assert [g["concept"] for g in groups] == [
        "itching", "leprosy_contact", "previous_leprosy_treatment", "referred_by",
        "case_category", "leprosy_classification", "gender",
    ]


def test_options_and_labels_are_strings():
    """YAML reads Yes/No as booleans; a quoted value keeps the record readable."""
    for group in ticks.groups(ticks.load_spec()):
        assert all(isinstance(option, str) for option in group["options"]), group["concept"]
        assert all(isinstance(label, str) for label in group["labels"]), group["concept"]
        assert len(group["options"]) == len(group["labels"]) == len(group["bands"])


def test_prompt_names_every_band_and_the_question():
    group = ticks.groups(ticks.load_spec())[3]
    text = ticks.prompt(group)
    assert group["question"] in text
    for index in range(1, len(group["labels"]) + 1):
        assert f"band {index}" in text
    assert "handwritten tick" in text


def test_decode_accepts_an_array_a_number_and_a_string():
    assert ticks.decode([2]) == [2]
    assert ticks.decode(2) == [2]
    assert ticks.decode("1, 3") == [1, 3]
    assert ticks.decode(None) == []


def test_back_reaction_strips_are_ten_horizontal_pairs():
    from core import ticks

    spec = ticks.load_spec("mozambique", "back")
    groups = ticks.groups(spec)
    assert len(groups) == 10
    assert all(group["kind"] == "horizontal" for group in groups)
    assert [group["concept"] for group in groups] == [
        "nerve_pain_spontaneous",
        "recent_sensitivity_change",
        "recent_patch_change",
        "painful_skin_lesions",
        "infiltrated_skin_lesions",
        "warm_skin_lesions",
        "red_skin_lesions",
        "skin_lesion_ulcerated",
        "nerve_tenderness_present",
        "eye_redness_pain",
    ]


def test_back_band_geometry_is_xywh_not_xyxy():
    spec = ticks.load_spec("mozambique", "back")
    for group in ticks.groups(spec):
        x1, y1, x2, y2 = group["strip"]
        for x, y, w, h in group["bands"]:
            assert 0 < w <= x2 - x1
            assert 0 < h <= y2 - y1
            assert x + w <= x2 and y + h <= y2


def test_ink_decides_blank_and_single_tick_without_a_model_call(tmp_path):
    import numpy as np

    class NeverCalled:
        def complete(self, *args, **kwargs):
            raise AssertionError("the model must not be called for blank/single ink")

    groups = ticks.groups(ticks.load_spec())
    ink = {group["concept"]: [0.0] * len(group["bands"]) for group in groups}
    ink["itching"][1] = 0.2  # band 2 is ticked
    values, usage = ticks.read_ticks(
        np.zeros((20, 20, 3), dtype=np.uint8),
        "mozambique",
        NeverCalled(),
        {"id": "fake", "model": "fake", "settings": {}},
        tmp_path,
        page="front",
        ink_by_concept=ink,
    )
    assert values["itching"]["value"] == "No"
    assert values["itching"]["band"] == 2
    assert values["leprosy_contact"]["blank"] is True
    assert values["leprosy_contact"]["value"] is None
    assert usage["calls"] == len(groups)
    assert usage["output_tokens"] == 0
