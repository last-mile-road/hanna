"""Prompt generation from the form contract.

One template, filled from the concept index. No prompt strings per form and no
field names hard-coded in the extractor.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

from .forms import Block, Pair, prompt_options

PROMPT_VERSION = "v1"

BLOCK_GUIDANCE = {
    "value_pairs": (
        "This crop is one region of a printed form with handwritten answers. "
        "Each field below has a printed label and a handwritten value next to or "
        "below it. Read the handwritten value, never the printed label. If a line "
        "has nothing handwritten on it, answer null — never the label, a dash or "
        "the word 'blank'."
    ),
    "tick_grid": (
        "This crop is a grid of printed options. The clinician marks one option "
        "with a tick, a cross or a handwritten sign. For each field, read which "
        "cell is marked. A plus or minus sign is a mark, not text. If no cell is "
        "marked, answer null. The printed words Yes/No, sim/não or True/False are "
        "the column headings, never the answer."
    ),
    "annotated_table": (
        "This crop is a hand-written table. Read the cell that belongs to each "
        "field, keeping its row and column position in mind. Do not shift values "
        "between adjacent rows. A cell with nothing written in it is null; a mark "
        "in a neighbouring cell does not fill it. Printed row and column headings "
        "are labels, never values."
    ),
    "dose_grid": (
        "This crop is a three-row by twelve-month treatment grid. Read the year, the "
        "day written in the month column, and any F that marks a missed month."
    ),
    "visual_annotation": (
        "This crop contains a body diagram or a drawing. Interpret the marks that "
        "are actually drawn. Count distinct marks only where a field asks for a "
        "count. Printed body outlines are not marks, and an unmarked area is null, "
        "not zero."
    ),
    "free_text": "This crop contains handwritten free text. Read it as written.",
    "sketch": "This crop contains a sketch. Read only what each field asks for.",
}

VALUE_GUIDANCE = {
    "tick_box": 'answer with "+" or "-" exactly',
    "choice_single": "answer with one of the allowed values",
    "integer": "answer with a whole number",
    "percent": "answer with a number",
    "date": "answer with the date as YYYY-MM-DD",
    "date_or_age": "answer with the year as written, or a date as YYYY-MM-DD",
    "code_number": "answer with the characters exactly as written",
    "phone_number": "answer with the digits exactly as written",
    "person_name": "answer with the name exactly as written",
    "text": "answer with the handwritten text exactly as written",
    "json": "answer with the structured object described in the instructions",
    "date_or_age": 'the card may carry a birth year or an age; if it carries an age, write it '
                   'as the card does (for example "28A" or "28 anos"); never turn an age into a year',
}


def _hint(concept: dict[str, Any], limit: int = 150) -> str:
    note = (concept.get("note") or "").strip()
    if not note:
        return ""
    first = note.split(". ")[0].rstrip(".")
    return first[:limit]


def _field_line(pair: Pair, concept: dict[str, Any]) -> str:
    name = concept.get("who_name") or pair.concept
    kind = pair.value_type
    parts = [f'- "{pair.id}": {name}', f"type: {kind}"]
    if pair.label_local:
        parts.append(f'card label: "{pair.label_local}"')

    options = prompt_options(pair, concept)
    if kind == "tick_box":
        parts.append('write "+" or "-"')
    elif options and kind in {"choice_single", "tick_box"}:
        parts.append("allowed values: " + " | ".join(str(o) for o in options))
        if 2 <= len(options) <= 8:
            # Read the box, not the option. The reviewer sees which boxes were marked, and
            # two ticked boxes or none is visible instead of silently resolved.
            boxes = ", ".join(f'"{o}": "ticked" | "unticked"' for o in options)
            parts.append(
                f'also answer "{pair.id}_boxes" as an object with every option: {{{boxes}}}'
            )

    marks = concept.get("card_marks")
    if marks:
        mapping = ", ".join(f"{k} = {v}" for k, v in marks.items())
        parts.append(f"card marks: {mapping}; return the English term")

    guidance = VALUE_GUIDANCE.get(kind)
    if guidance:
        parts.append(guidance)

    hint = _hint(concept)
    if hint and kind != "text":
        parts.append(f"note: {hint}")
    return " | ".join(parts)


TREATMENT_GRID_PROMPT = """You are reading the treatment grid of a {country} leprosy clinic card.

The grid has three rows, one per course year. Each row has the year written on
the left, then twelve month columns from Janeiro to Dezembro. Under the day
number is a signature line (Rubrica/Faltou).

For each row read:
- the year in the left column
- every month cell that contains a day number (the day the supervised blister
  pack was given); a signature or mark under the day confirms the dose
- a large F written in a month cell means the patient missed that month

Return one JSON object and nothing else, in this shape:
{{"treatment_grid": {{"rows": [{{"year": 2025, "doses": [{{"month": 5, "day": 31}}], "missed_months": []}}]}}}}

Use month numbers 1-12. Omit empty months. Use null for an empty row. Do not
invent doses. If a date has no year, use null for the year. The handwriting is
{language}.
"""


def build_prompt(
    block: Block,
    pairs: list[Pair],
    concepts: dict[str, dict[str, Any]],
    country: str = "Mozambique",
    language: str = "Portuguese",
    with_template: bool = False,
) -> str:
    if block.block_type == "dose_grid":
        return TREATMENT_GRID_PROMPT.format(country=country, language=language)
    guidance = BLOCK_GUIDANCE.get(block.block_type, BLOCK_GUIDANCE["value_pairs"])
    lines = [
        f"You are reading one crop of a hand-written leprosy clinic card from {country}.",
        f"The handwriting is {language}. The block is '{block.id}' ({block.block_type}).",
    ]
    if with_template:
        lines.append(
            "Two images are attached: the first is the filled card crop, the second "
            "is the blank template at the same position. Printed content appears in "
            "both. Handwriting and marks appear only in the first. Ignore printed "
            "marks and count only what was added."
        )
    if block.hint:
        lines += ["", block.hint.strip()]
    lines += [
        "",
        guidance,
        "",
        "Fields to read:",
    ]
    for pair in pairs:
        if pair.derived:
            continue
        lines.append(_field_line(pair, concepts[pair.concept]))

    lines += [
        "",
        "Return one JSON object and nothing else.",
        'Use the field id as the key, for example {"patient_name": "..."}.',
        "Use null when a field is blank or cannot be read.",
        "A handwritten 0 is a value: return 0, not null, when a zero is written.",
        "Keep all keys listed above and add no others.",
        "Do not return markdown, explanations or a confidence field.",
        "If a mark is visible but the reading is uncertain, return your best reading.",
    ]
    return "\n".join(lines)


def prompt_hash(prompt: str, model: str, settings: dict[str, Any]) -> str:
    payload = json.dumps(
        {"prompt": prompt, "model": model, "settings": settings, "version": PROMPT_VERSION},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode()).hexdigest()
