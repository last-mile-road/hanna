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


def test_reaction_grid_prompt_names_rows_and_blank():
    text = ticks.reaction_prompt(1, 5)
    assert "rows 1 to 5" in text
    assert "sim" in text and "não" in text and "blank" in text
    assert "even spacing" in text


def test_reaction_grid_render_is_numbered_and_spaced(tmp_path):
    import numpy as np
    from tools.render_tick_strip import render_rows

    image = np.full((120, 200, 3), 180, dtype=np.uint8)
    rows = [[20, 10, 80, 40], [20, 50, 80, 80], [20, 90, 80, 118]]
    rendered = render_rows(image, rows, start=1, gap=12)
    assert rendered.shape[0] > 3 * (30 + 12)
    assert rendered.shape[1] >= 80
