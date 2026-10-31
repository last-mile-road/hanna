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
