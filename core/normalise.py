"""Canonicalise a value and compare it with the ground truth.

The sheet vocabulary is the card's vocabulary: `M`, `re_entry`, `Weak`. The
concept index holds both that and the WHO term, so one function can move between
them. This layer is deliberately small and testable; it is the seed of the
validation service, not a substitute for it.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

EMPTY_MARKERS = {
    "",
    "empty",
    "null",
    "none",
    "n/a",
    "na",
    "unknown",
    "not filled",
    "not recorded",
    "sem informação",
    "sem informacao",
    "não preenchido",
    "nao preenchido",
}

GENDER = {
    "m": "M",
    "male": "M",
    "masculino": "M",
    "masculine": "M",
    "f": "F",
    "female": "F",
    "feminino": "F",
    "feminine": "F",
}

MUSCLE = {
    "n": "Normal",
    "normal": "Normal",
    "f": "Weak",
    "fraco": "Weak",
    "fraca": "Weak",
    "weak": "Weak",
    "a": "Paralysed",
    "ausente": "Paralysed",
    "paralysed": "Paralysed",
    "paralyzed": "Paralysed",
    "paralisado": "Paralysed",
    "paralisada": "Paralysed",
}

YES_NO = {
    "yes": "Yes",
    "sim": "Yes",
    "true": "Yes",
    "1": "Yes",
    "no": "No",
    "não": "No",
    "nao": "No",
    "false": "No",
    "0": "No",
}

TICK = {
    "+": "+",
    "plus": "+",
    "positive": "+",
    "present": "+",
    "yes": "+",
    "sim": "+",
    "1": "+",
    "-": "-",
    "minus": "-",
    "negative": "-",
    "absent": "-",
    "no": "-",
    "não": "-",
    "nao": "-",
    "0": "-",
}

DATE_FORMATS = ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%Y/%m/%d", "%d %m %Y")

# Patients often do not know their birth year and the card records an age instead:
# "28A", "28 A", "28 anos", "idade 28", "28y". The A is not a digit.
AGE_PATTERN = re.compile(r"^\s*(?:idade\s*)?(\d{1,3})\s*(?:a|aa|anos?|years?|y|idade)?\s*$", re.IGNORECASE)


def age_from_text(value: Any) -> int | None:
    """Return a plausible age in years, or None. 284 is not an age and not a year."""
    text = "" if value is None else str(value).strip()
    match = AGE_PATTERN.match(text)
    if not match:
        return None
    number = int(match.group(1))
    if 5 <= number <= 120:
        return number
    return None


def birth_year_from_age(value: Any, reference_year: int | None) -> int | None:
    age = age_from_text(value)
    if age is None or reference_year is None:
        return None
    return int(reference_year) - age


def key(value: Any) -> str:
    text = "" if value is None else str(value)
    text = unicodedata.normalize("NFKD", text)
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("_", " ").replace("-", " ").replace(".", " ")
    return re.sub(r"\s+", " ", text).strip()


def _clean(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().strip('"').strip("'").strip()
    # A literal plus or minus is a valid tick value, not an empty cell.
    if text in {"+", "-"}:
        return text
    if key(text) in EMPTY_MARKERS:
        return None
    return text


def _canonical_date(text: str) -> str:
    for fmt in DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            continue
    match = re.search(r"\b(19|20)\d{2}\b", text)
    return match.group(0) if match else text


def _portuguese_text(text: str) -> str:
    """Small, explicit clinical-spelling variants. Not a phonetic rewrite."""
    text = re.sub(r"\b(\d+)\s+amos\b", r"\1 anos", text, flags=re.IGNORECASE)
    text = re.sub(r"\bnas contas\b", "nas costas", text, flags=re.IGNORECASE)
    return text


def canonical(
    value_type: str,
    value: Any,
    concept: dict[str, Any] | None = None,
    reference_year: int | None = None,
) -> str | None:
    concept = concept or {}
    text = _clean(value)

    if value_type == "tick_box":
        # Clinically a nerve is enlarged or it is not: only a clear plus is positive.
        # A blank cell, tilde, tick, dot or smudge all mean "not enlarged". A blank
        # cell in a printed nerve table is a minus, not an unread value.
        if text is None:
            return "-"
        return "+" if TICK.get(key(text)) == "+" else "-"

    if text is None:
        return None
    k = key(text)

    concept_id = concept.get("id", "")

    if concept_id == "gender":
        return GENDER.get(k, text)

    if (
        concept_id.startswith("hand_ulnar")
        or concept_id.startswith("foot_dorsiflex")
        or concept_id.startswith("finger_abduction")
    ):
        return MUSCLE.get(k, text)

    if concept_id.startswith("eye_lagophthalmos"):
        # A negative millimetre reading is a misread minus sign; the measurement
        # itself is taken as positive. 0 means full closure.
        match = re.search(r"-?\d+(?:[.,]\d+)?", text)
        if match:
            return str(abs(int(float(match.group(0).replace(",", ".")))))
        return text

    if value_type == "integer" or value_type == "percent":
        match = re.search(r"-?\d+(?:[.,]\d+)?", text)
        if match:
            number = match.group(0).replace(",", ".")
            return str(int(float(number))) if number.endswith(".0") else number
        return text

    if value_type == "date":
        return _canonical_date(text)

    if value_type == "date_or_age":
        if re.fullmatch(r"\d{4}", text):
            return text
        derived = birth_year_from_age(text, reference_year)
        if derived is not None:
            # The card held an age; the record needs a birth year. The raw age stays in
            # the observation so the reviewer can see where the year came from.
            return str(derived)
        return _canonical_date(text)

    if value_type == "phone_number":
        digits = re.sub(r"\D", "", text)
        return digits or text

    if value_type in {"choice_single", "tick_box"}:
        for alias, term in (concept.get("value_aliases") or {}).items():
            if key(alias) == k:
                return str(term)
        lookup: dict[str, str] = {}
        for option in concept.get("observed") or []:
            lookup[key(option)] = str(option)
        for option in concept.get("options") or []:
            lookup.setdefault(key(option), str(option))
        for mark, term in (concept.get("card_marks") or {}).items():
            lookup.setdefault(key(mark), str(term))
            lookup.setdefault(key(term), str(term))
        if k in YES_NO:
            # The model may answer in English while the card/options are in
            # Portuguese (sim/não) or booleans (true/false). Keep the label the
            # concept declares, whichever language family it uses.
            positive = {"yes", "sim", "true", "1"}
            negative = {"no", "nao", "não", "false", "0"}
            wanted = positive if YES_NO[k] == "Yes" else negative
            for label in lookup.values():
                if key(label) in wanted:
                    return label
            if any(key(o) in {"yes", "no"} for o in lookup):
                return YES_NO[k]
        if k in GENDER and any(key(o) in {"m", "f", "male", "female"} for o in lookup):
            return GENDER[k]
        return lookup.get(k, text)

    if value_type == "text":
        text = _portuguese_text(text)
    return re.sub(r"\s+", " ", text).strip()


def compare(value_type: str, got: Any, expected: Any, concept: dict[str, Any] | None = None) -> bool | None:
    """True, False, or None when the expected value is not scoreable."""
    expected_c = canonical(value_type, expected, concept)
    if expected_c is None:
        return None
    got_c = canonical(value_type, got, concept)
    if got_c is None:
        return False

    if value_type in {"integer", "percent"}:
        try:
            return float(got_c) == float(expected_c)
        except (TypeError, ValueError):
            return key(got_c) == key(expected_c)

    if value_type in {"date", "date_or_age"}:
        return got_c == expected_c

    if value_type in {"text", "person_name", "code_number"}:
        a = key(got_c)
        variants = [expected_c]
        if value_type == "text" and " - " in expected_c:
            variants.append(expected_c.rsplit(" - ", 1)[0])
        got_digits = [d for d in re.findall(r"\d+", got_c)]
        for variant in variants:
            b = key(variant)
            if a == b:
                return True
            if value_type == "text":
                # A changed year or count is a real error, not a spelling variant.
                expected_digits = [d for d in re.findall(r"\d+", variant)]
                if expected_digits and expected_digits != got_digits:
                    continue
                if len(b) > 5 and SequenceMatcher(None, a, b).ratio() >= 0.9:
                    return True
        return False

    return key(got_c) == key(expected_c)
