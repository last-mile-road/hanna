import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import normalise as n


def test_tick_minus_is_a_value_not_empty():
    concept = {"id": "nerve_pain_x", "value_type": "tick_box", "options": ["+", "-"]}
    assert n.canonical("tick_box", "-", concept) == "-"
    assert n.compare("tick_box", "-", "-", concept) is True
    assert n.compare("tick_box", "+", "-", concept) is False


def test_gender_card_marks():
    concept = {"id": "gender", "value_type": "choice_single", "observed": ["M", "F"]}
    assert n.canonical("choice_single", "Male", concept) == "M"
    assert n.canonical("choice_single", "feminino", concept) == "F"
    assert n.compare("choice_single", "M", "M", concept) is True


def test_muscle_terms():
    concept = {
        "id": "hand_ulnar_right",
        "value_type": "choice_single",
        "options": ["Normal", "Weak", "Paralysed"],
        "card_marks": {"N": "Normal", "F": "Weak", "A": "Paralysed"},
    }
    assert n.canonical("choice_single", "F", concept) == "Weak"
    assert n.canonical("choice_single", "Ausente", concept) == "Paralysed"
    assert n.compare("choice_single", "Normal", "Normal", concept) is True


def test_yes_no_portuguese():
    concept = {"id": "itching", "value_type": "choice_single", "observed": ["Yes", "No"]}
    assert n.canonical("choice_single", "Sim", concept) == "Yes"
    assert n.canonical("choice_single", "não", concept) == "No"


def test_yes_no_keeps_the_card_vocabulary():
    # The 8B answers in English even when the card and options are Portuguese.
    # The stored value must stay the option the concept declares, or validation
    # rejects a correct read as "fora das opções permitidas".
    concept = {"id": "itching", "value_type": "choice_single", "observed": ["sim", "não"]}
    assert n.canonical("choice_single", "Yes", concept) == "sim"
    assert n.canonical("choice_single", "No", concept) == "não"
    assert n.canonical("choice_single", True, concept) == "sim"
    boolean = {"id": "itching", "value_type": "choice_single", "options": [True, False]}
    assert n.canonical("choice_single", "sim", boolean) == "True"
    assert n.canonical("choice_single", "No", boolean) == "False"


def test_numbers_and_dates():
    assert n.canonical("integer", "4 patches") == "4"
    assert n.canonical("integer", "2.0") == "2"
    assert n.canonical("date", "31/05/2025") == "2025-05-31"
    assert n.canonical("date", "2025-05-31") == "2025-05-31"


def test_text_ignores_accents_but_not_meaning():
    assert n.compare("person_name", "Jose da Silva", "José da Silva") is True
    assert n.compare("text", "Na Cava", "Na cara") is False


def test_empty_returns_none():
    assert n.canonical("text", "empty") is None
    assert n.canonical("integer", "") is None
    assert n.compare("integer", None, "4") is False


def test_text_with_english_gloss_is_accepted():
    assert n.compare(
        "text",
        "A partir do ano passado",
        "A partir do ano passado - since last year",
    ) is True
    assert n.compare("text", "Na Cava", "Na cara - in the face") is False


def test_value_alias_maps_card_portuguese():
    concept = {
        "id": "referred_by",
        "value_type": "choice_single",
        "observed": ["family member", "volunteer"],
        "value_aliases": {"trazido por familiar": "family member"},
    }
    assert n.canonical("choice_single", "trazido por familiar", concept) == "family member"


def test_portuguese_clinical_variants():
    assert n.compare("text", "Ha 2 anos", "Há 2 amos - 2 years") is True
    assert n.compare("text", "Nas costas", "Nas contas - on the back") is True


def test_lagophthalmos_negative_sign_is_ignored():
    concept = {"id": "eye_lagophthalmos_right", "value_type": "integer"}
    assert n.canonical("integer", "-1", concept) == "1"
    assert n.canonical("integer", "-0", concept) == "0"
    assert n.canonical("integer", "12 mm", concept) == "12"


def test_age_is_converted_to_a_birth_year_when_the_card_has_no_year():
    """Patients often give an age: 28A, 28 anos, idade 28. The record needs a year."""
    for raw in ("28A", "28 a", "28 anos", "idade 28", "28y"):
        assert n.canonical("date_or_age", raw, None, reference_year=2026) == "1998", raw
    # A four-digit year is untouched, and a date still canonicalises as a date.
    assert n.canonical("date_or_age", "1984", None, reference_year=2026) == "1984"
    assert n.canonical("date_or_age", "28/05/1984", None, reference_year=2026) == "1984-05-28"


def test_a_mangled_three_digit_value_is_not_mistaken_for_an_age():
    """284 is neither a year nor an age; it must stay visible so validation can complain."""
    assert n.age_from_text("284") is None
    assert n.age_from_text("28A") == 28
    assert n.canonical("date_or_age", "284", None, reference_year=2026) == "284"


def test_tick_box_blank_is_a_minus():
    # The clinic rule: only a clear plus is positive; a blank cell in a printed
    # nerve table is a minus, not an unread value.
    assert n.canonical("tick_box", None) == "-"
    assert n.canonical("tick_box", "·") == "-"
    assert n.canonical("tick_box", "+") == "+"
