"""Table readers: MinerU standard, called through the HANNA box.

Why a reader instead of the VLM: on the 2026-10-30 measurement MinerU read the
disability table 98/98 and the nerve grid 84/84 cell by cell, against the routed VLM's
89/98 and 79/84. On form16 the same 9B got all six disability grades and two nerve
boxes wrong by taking the left column for the right, while MinerU's table reproduces
the ground truth exactly.

The pipeline treats a reader as a model that answers with JSON: `Reading.text` is the
same `{"pair_id": "value"}` object a VLM would return, so normalisation and scoring run
unchanged. No token counts, no cost — the box is billed by the second.

    MINERU_URL=https://<box>/parse/mineru
    MINERU_KEY=<the box's HANNA_KEY>
"""

from __future__ import annotations

import html
import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import settings

SUPPORTED_BLOCKS = {
    "def_exam", "nerve_assessment", "prednisolone",
    "visit1_nerve", "visit2_nerve", "visit3_nerve", "visit4_nerve",
    "visit1_def_exam", "visit2_def_exam", "visit3_def_exam", "visit4_def_exam",
}

# The treatment grid is deliberately NOT routed here. MinerU at tier=advanced returns
# the grid as a table (standard returns an image), and `_treatment` below maps a clean
# one — but measured on four cards the cells are unreliable: form21's single recorded
# dose is absent from the parse, and form25's year lives in the header row. The 9B reads
# treatment at 21/23, so it keeps that block.
READER_TIERS = {"def_exam": "standard", "nerve_assessment": "standard",
                "prednisolone": "standard", "treatment_follow": "advanced"}
READER_TIERS.update({f"visit{n}_nerve": "standard" for n in (1, 2, 3, 4)})
READER_TIERS.update({f"visit{n}_def_exam": "standard" for n in (1, 2, 3, 4)})


@dataclass
class Reading:
    """Duck-types `inference.Completion` so `run.record()` needs no special case."""

    text: str
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None
    latency_ms: int = 0
    usage: dict[str, Any] = field(default_factory=dict)


def table_matrix(fragment: str) -> list[list[str]]:
    """Expand the first <table> into a rectangular grid, applying colspan/rowspan."""
    match = re.search(r"<table[^>]*>(.*?)</table>", fragment, re.S | re.I)
    if not match:
        return []
    body = match.group(1)
    rows = re.findall(r"<tr[^>]*>(.*?)</tr>", body, re.S | re.I)
    grid: list[list[str | None]] = []
    spans: dict[tuple[int, int], int] = {}
    for r_index, row in enumerate(rows):
        cells = re.findall(r"<t[dh][^>]*>.*?</t[dh]>", row, re.S | re.I)
        out: list[str | None] = []
        col = 0
        for cell in cells:
            while (r_index, col) in spans:
                out.append(None)
                col += 1
            text = re.sub(r"<[^>]+>", " ", cell)
            text = html.unescape(re.sub(r"\s+", " ", text)).strip()
            cspan = re.search(r'colspan="?(\d+)', cell, re.I)
            rspan = re.search(r'rowspan="?(\d+)', cell, re.I)
            width = int(cspan.group(1)) if cspan else 1
            height = int(rspan.group(1)) if rspan else 1
            out.append(text)
            for extra in range(1, width):
                out.append(None)
            if height > 1:
                for down in range(1, height):
                    spans[(r_index + down, col)] = width
            col += width
        while (r_index, col) in spans:
            out.append(None)
            col += 1
        grid.append(out)
    width = max((len(r) for r in grid), default=0)
    return [[cell if cell is not None else "" for cell in row + [""] * (width - len(row))]
            for row in grid]


def _fold(value: str) -> str:
    import unicodedata

    folded = unicodedata.normalize("NFKD", value or "")
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).lower().strip()


# --- disability examination ---------------------------------------------------------
# Left-to-right on the card: the first value column is the right side, the last is the
# left. Matching is on the printed Portuguese label.
DEF_EXAM_ROWS = [
    (("pestanejo",), ("right_eye_blinking", "left_eye_blinking"), {"+": "Yes", "-": "No"}),
    (("lagoftalmia",), ("right_eye_lagoft", "left_eye_lagoft"), {}),
    (("abducao", "dedos"), ("right_hipothenar", "left_hipothenar"), {}),
    (("dorsi",), ("right_foot_dorsiflex", "left_foot_dorsiflex"), {}),
    # The eye section header is not a value row; the grade rows carry ehf_*.
    (("exame dos olhos",), None, {}),
    (("testagem",), None, {}),  # section header; no values
    (("graus de deformidade",), None, {}),
]

# The three grade rows sit under the deformation heading and carry only the site name.
DEF_EXAM_GRADES = [
    (("olhos",), ("ehf_right_eye", "ehf_left_eye")),
    (("maos",), ("ehf_right_hand", "ehf_left_hand")),
    (("pes",), ("ehf_right_foot", "ehf_left_foot")),
]

NERVE_NAMES = [
    ("cubital", "cubital"),
    ("radial", "radial_cutaneous"),
    # The card spells it both "Poplíteo" and "Popilíteo". Normalise the second
    # spelling below, then match the shared stem.
    ("popl", "popliteal_external"),
]


def _sides(row: list[str]) -> tuple[str, str] | None:
    """First and last non-empty value cell: the D and E value columns.

    The D/E column headings are repeated in some MinerU tables; they are labels,
    not values, and must not become a grade or a muscle result.
    """
    values = [
        cell for cell in row
        if cell.strip()
        and cell.strip() not in {"—", "–"}
        and _fold(cell) not in {"d", "e", "direito", "esquerdo"}
    ]
    if len(values) < 2:
        return None
    return values[0], values[-1]


# Handwriting glyphs that stand in for digits on these cards: ○ for 0, / or I for 1,
# Z for 2. Measured on form19, where the clinician wrote circles instead of noughts.
GLYPHS = {"○": "0", "o": "0", "O": "0", "c": "0", "C": "0",
          "/": "1", "l": "1", "I": "1", "i": "1",
          "z": "2", "Z": "2", "s": "2", "S": "2"}


def _number(value: str) -> str:
    text = (value or "").strip()
    digits = re.sub(r"\D", "", text)
    if digits:
        return digits
    mapped = "".join(GLYPHS.get(char, "") for char in text)
    return mapped or text


def latex_matrix(fragment: str) -> list[list[str]]:
    """A LaTeX `\begin{array}{...}` block as a rectangular grid.

    MinerU returns a small table crop either as an HTML <table> or as a LaTeX
    array, depending on how its layout model classified the image. Both are the
    same table; parsing only one of them silently empties half the cards.
    """
    match = re.search(r"\\begin\{array\}\{[^}]*\}(.*?)\\end\{array\}", fragment, re.S)
    if not match:
        return []
    body = match.group(1)
    rows = re.split(r"\\\\", body)
    grid: list[list[str]] = []
    for row in rows:
        row = row.replace("\\hline", " ")
        cells = [cell.strip() for cell in row.split("&")]
        if not any(cells):
            continue
        cleaned = []
        for cell in cells:
            text = re.sub(r"\\text\s*\{([^}]*)\}", r"\1", cell)
            text = re.sub(r"\\[a-zA-Z]+", " ", text)
            text = text.replace("$", " ").replace("{", " ").replace("}", " ")
            cleaned.append(re.sub(r"\s+", " ", text).strip())
        grid.append(cleaned)
    width = max((len(row) for row in grid), default=0)
    return [row + [""] * (width - len(row)) for row in grid]


def matrix(fragment: str) -> list[list[str]]:
    """The table in whichever shape MinerU returned it."""
    grid = table_matrix(fragment)
    if grid:
        return grid
    return latex_matrix(fragment)


def _def_exam(markdown: str) -> dict[str, str]:
    grid = matrix(markdown)
    if not grid:
        return {}
    out: dict[str, str] = {}
    seen_grades = False
    for row in grid:
        labels = [_fold(cell) for cell in row]
        joined = " ".join(labels)
        if "graus de deformidade" in joined:
            seen_grades = True
            continue
        # Grade rows come after the deformation heading and are labelled by site only.
        if seen_grades:
            for keys, pair_ids in DEF_EXAM_GRADES:
                if any(label == key for label in labels for key in keys):
                    sides = _sides([c for c in row if _fold(c) not in keys])
                    if sides:
                        out[pair_ids[0]], out[pair_ids[1]] = sides[0], sides[1]
                    break
            if "grau maximo" in joined or "grau total" in joined:
                seen_grades = False
            continue
        for keys, pair_ids, mapping in DEF_EXAM_ROWS:
            if pair_ids and any(key in joined for key in keys):
                sides = _sides([c for c in row if not any(k in _fold(c) for k in keys)])
                if sides:
                    out[pair_ids[0]] = mapping.get(sides[0], sides[0])
                    out[pair_ids[1]] = mapping.get(sides[1], sides[1])
                break
    # The muscle rows are written as one letter, N / F / A, with the legend
    # printed on the card: N=Normal, F=Fraca (weak), A=Ausente (paralysed).
    # Store the words the form offers, not the letters.
    muscle = {
        "n": "Normal", "normal": "Normal",
        "f": "Weak", "fraca": "Weak", "fraco": "Weak", "weak": "Weak",
        "a": "Paralysed", "ausente": "Paralysed", "paralisado": "Paralysed",
        "p": "Paralysed",
    }
    for pair_id in ("right_hipothenar", "left_hipothenar",
                    "right_foot_dorsiflex", "left_foot_dorsiflex"):
        value = out.get(pair_id)
        if value is None:
            continue
        out[pair_id] = muscle.get(_fold(value), value)
    # Grades and eyelid gaps are numbers, and the clinicians write ○ for nought.
    for pair_id in list(out):
        if pair_id.startswith("ehf_") or pair_id.endswith("_lagoft"):
            out[pair_id] = _number(out[pair_id])
    return out


def _nerve(markdown: str) -> dict[str, str]:
    """Three nerves across, two sides each; one row for enlargement, one for pain.

    The back card prints a section title row above the nerve names and the front
    does not, so the header is looked for in the first rows rather than assumed
    to be row one. Columns are read by their position in the header, not by
    dropping empty cells: dropping them shifts every mark after the first blank.
    """
    grid = matrix(markdown)
    if not grid:
        return {}
    header_index = None
    for index, row in enumerate(grid[:4]):
        joined = _fold(" ".join(row)).replace("popiliteo", "popliteo")
        if any(fragment in joined for fragment, _ in NERVE_NAMES):
            header_index = index
            break
    if header_index is None:
        return {}
    header = [_fold(cell).replace("popiliteo", "popliteo") for cell in grid[header_index]]
    ordered = [
        name for fragment, name in NERVE_NAMES
        if any(fragment in cell for cell in header)
    ]
    if not ordered:
        return {}
    # Find the D/E sub-header: it gives the value columns exactly, whatever the
    # header row's colspans look like. Reading order of the pairs is the order of
    # the nerves on the card (cubital, radial, popliteal).
    starts: dict[str, int] = {}
    for row_index in range(header_index + 1, min(header_index + 4, len(grid))):
        folded = [_fold(cell) for cell in grid[row_index]]
        if sum(1 for cell in folded if cell in {"d", "e"}) < 4:
            continue
        pairs: list[tuple[int, int]] = []
        current: int | None = None
        for column, cell in enumerate(folded):
            if cell == "d":
                current = column
            elif cell == "e" and current is not None:
                pairs.append((current, column))
                current = None
        if len(pairs) >= len(ordered):
            # A D/E row without a label cell starts at the first value column;
            # the data rows all carry the row label first, so shift by one.
            offset = 0 if folded and folded[0] not in {"d", "e"} else 1
            starts = {
                name: pairs[index][0] + offset
                for index, name in enumerate(ordered)
            }
        break
    if not starts:
        # No D/E row: fall back to the header's own column positions.
        for fragment, concept_name in NERVE_NAMES:
            for column, cell in enumerate(header):
                if fragment in cell:
                    starts[concept_name] = column + 1
                    break
    if not starts:
        return {}
    out: dict[str, str] = {}
    needed = max(starts.values()) + 2
    for row in grid[header_index + 1 :]:
        label = _fold(row[0]) if row else ""
        if label in {"grosso", "growth", "aumento"}:
            kind = "enlargement"
        elif label in {"dor", "pain"}:
            kind = "pain"
        else:
            continue
        if len(row) < needed:
            # MinerU dropped the trailing cells on this row. A wrong column is
            # worse than an unread table: it invents a positive mark.
            return {}
        for concept_name, column in starts.items():
            right = row[column].strip() if column < len(row) else ""
            left = row[column + 1].strip() if column + 1 < len(row) else ""
            # The clinic's rule: only a clear plus (or a cross/tick) is positive.
            # Everything else — blank, dot, dash, tilde, smudge — is negative.
            out[f"nerve_{kind}_{concept_name}_right"] = (
                "+" if any(mark in right for mark in "+xX✗✓") else "-"
            )
            out[f"nerve_{kind}_{concept_name}_left"] = (
                "+" if any(mark in left for mark in "+xX✗✓") else "-"
            )
    return out


def pipe_table(markdown: str) -> list[list[str]]:
    """Parse a MinerU pipe table (some local outputs use markdown, not HTML)."""
    rows: list[list[str]] = []
    for line in (markdown or "").splitlines():
        line = line.strip()
        if not (line.startswith("|") and line.endswith("|")):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        if cells and all(set(cell) <= set("-: ") for cell in cells):
            continue
        rows.append(cells)
    return rows


def _prednisolone(markdown: str) -> dict[str, str]:
    """The 12-week prednisolone taper table: date, week, daily dose, responsible."""
    grid = table_matrix(markdown) or pipe_table(markdown)
    if not grid:
        return {}
    rows: list[dict[str, str]] = []
    for row in grid:
        folded = " ".join(_fold(cell) for cell in row)
        if "semana" in folded and "dose" in folded:
            continue
        if not any(cell.strip() for cell in row):
            continue
        cells = (row + [""] * 4)[:4]
        if not (cells[1].strip().isdigit() or cells[2].strip()):
            continue
        rows.append({
            "data": cells[0].strip(),
            "semana": cells[1].strip(),
            "dose": cells[2].strip(),
            "responsavel": cells[3].strip(),
        })
    if not rows:
        return {}
    # The dose column is printed. Only a handwritten date or signature proves the
    # week was actually given. No handwriting at all means the block is blank, not
    # a completed 12-week course.
    recorded = [
        row for row in rows
        if row["data"].strip() or row["responsavel"].strip()
    ]
    if not recorded:
        return {}
    status = "completed" if len(recorded) >= 12 and len(rows) >= 12 else "in_progress"
    return {
        "prednisolone_status": status,
        "prednisolone_weeks_recorded": str(len(recorded)),
        "prednisolone_table": json.dumps(rows, ensure_ascii=False),
    }


def _treatment(markdown: str) -> dict[str, str]:
    """The 3×12 TMA grid: a day number per month, or F for a missed dose.

    Only available at tier=advanced; at standard the layout model calls this region a
    chart. Each year occupies TWO printed rows — `Dia` carries the day of the dose and
    `Rubrica/Faltou` the signature or a missed dose — and the year itself is often read
    as `20.25` for 2025, so it is parsed digit by digit rather than matched.

    The grid is emitted in the shape `treatment.derive_treatment_grid` expects, so the
    start date, dose count, month count and missed count are derived, not read.
    """
    from .treatment import MONTHS

    folded = {_fold(name): number for name, number in MONTHS.items()}
    grid = table_matrix(markdown)
    if not grid:
        return {}

    month_columns: dict[int, int] = {}
    header_row = None
    for index, row in enumerate(grid):
        found = {}
        for column, cell in enumerate(row):
            number = folded.get(_fold(cell))
            if number:
                found[column] = number
        if len(found) >= 6:
            month_columns, header_row = found, index
            break
    if not month_columns or header_row is None:
        return {}

    def year_of(row: list[str]) -> int | None:
        for cell in row[:3]:
            digits = re.sub(r"\D", "", cell or "")
            if len(digits) == 4 and digits[:2] in {"19", "20"}:
                return int(digits)
            if len(digits) == 4 and digits[:2] in {"20", "19"}:
                return int(digits)
        return None

    def day_of(cell: str) -> int | None:
        match = re.match(r"\s*(\d{1,2})\b", cell or "")
        return int(match.group(1)) if match else None

    rows: dict[int | None, dict] = {}
    current_year: int | None = None
    for row in grid[header_row + 1:]:
        row_year = year_of(row)
        if row_year is not None:
            current_year = row_year
        label = " ".join(_fold(cell) for cell in row[1:2])
        entry = rows.setdefault(current_year, {"year": current_year, "doses": [],
                                               "missed_months": []})
        is_missed_row = "falta" in label or "rubrica" in label
        for column, month in sorted(month_columns.items()):
            if column >= len(row):
                continue
            cell = (row[column] or "").strip()
            if not cell:
                continue
            if is_missed_row:
                if _fold(cell) in {"f", "falta", "faltou", "x"}:
                    entry["missed_months"].append(month)
                continue
            day = day_of(cell)
            if day is not None and not any(
                dose["month"] == month for dose in entry["doses"]
            ):
                entry["doses"].append({"month": month, "day": day})

    values = [entry for entry in rows.values()
              if entry["doses"] or entry["missed_months"] or entry["year"]]
    if not values:
        return {}
    for entry in values:
        entry["missed_months"] = sorted(set(entry["missed_months"]))
    return {"treatment_grid": json.dumps({"rows": values}, ensure_ascii=False)}


# The back visit tables are the same printed tables as the front. Map the front
# parser keys to the back pair ids; only the visit prefix and the eye/muscle/grade
# names change.
BACK_DEF_MAP = {
    "right_eye_blinking": "eye_blinking_right",
    "left_eye_blinking": "eye_blinking_left",
    "right_eye_lagoft": "eye_lagophthalmos_right",
    "left_eye_lagoft": "eye_lagophthalmos_left",
    "right_hipothenar": "finger_abduction_right",
    "left_hipothenar": "finger_abduction_left",
    "right_foot_dorsiflex": "foot_dorsiflex_right",
    "left_foot_dorsiflex": "foot_dorsiflex_left",
    "ehf_right_eye": "disability_grade_eye_right",
    "ehf_left_eye": "disability_grade_eye_left",
    "ehf_right_hand": "disability_grade_hand_right",
    "ehf_left_hand": "disability_grade_hand_left",
    "ehf_right_foot": "disability_grade_foot_right",
    "ehf_left_foot": "disability_grade_foot_left",
}


def _back_values(values: dict[str, str], visit: int, mapping: dict[str, str]) -> dict[str, str]:
    return {
        f"v{visit}_{mapping.get(key, key)}": value
        for key, value in values.items()
    }


READERS = {"def_exam": _def_exam, "nerve_assessment": _nerve,
           "prednisolone": _prednisolone, "treatment_follow": _treatment}
for _n in (1, 2, 3, 4):
    READERS[f"visit{_n}_nerve"] = (
        lambda markdown, n=_n: _back_values(_nerve(markdown), n, {})
    )
    READERS[f"visit{_n}_def_exam"] = (
        lambda markdown, n=_n: _back_values(_def_exam(markdown), n, BACK_DEF_MAP)
    )
# `_treatment` stays callable and tested even though the block is routed to the 9B: it is
# the only clean-grid mapper we have, and it works when MinerU's cells are right.


# --- transport ----------------------------------------------------------------------

def _box_wait(base: str, key: str, timeout: float = 60.0) -> None:
    """Wait briefly for the box when it answers 503 (scale-to-zero cold start).

    A full boot takes ~5–6 minutes. Blocking a card that long is worse than
    reading the table with the VLM for that card: wait a minute, and if the box
    is still not up, let the caller raise so the block falls back.
    """
    import urllib.parse

    parsed = urllib.parse.urlparse(base)
    root = f"{parsed.scheme}://{parsed.netloc}"
    headers = {"X-HANNA-Key": key} if key else {}
    ready_url = f"{root}/ready"
    warm_url = f"{root}/warm"
    started = time.monotonic()
    try:
        urllib.request.urlopen(urllib.request.Request(warm_url, headers=headers), timeout=60)
    except (urllib.error.URLError, OSError, ValueError):
        pass  # /warm may itself report not-ready; /ready is the authority
    while time.monotonic() - started < timeout:
        try:
            with urllib.request.urlopen(
                urllib.request.Request(ready_url, headers=headers), timeout=20
            ) as response:
                payload = json.load(response)
            if payload.get("ready"):
                return
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            pass
        time.sleep(10)


def _post_file(url: str, path: Path, key: str) -> dict:
    boundary = uuid.uuid4().hex
    head = (f"--{boundary}\r\n"
            f'Content-Disposition: form-data; name="file"; filename="{path.name}"\r\n'
            f"Content-Type: application/octet-stream\r\n\r\n").encode()
    body = head + path.read_bytes() + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(
        url, data=body, method="POST",
        headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                 **({"X-HANNA-Key": key} if key else {})},
    )
    try:
        with urllib.request.urlopen(request, timeout=900) as response:
            return json.load(response)
    except urllib.error.HTTPError as error:
        if error.code != 503:
            raise
        _box_wait(url, key)
        request = urllib.request.Request(
            url, data=body, method="POST",
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}",
                     **({"X-HANNA-Key": key} if key else {})},
        )
        with urllib.request.urlopen(request, timeout=900) as response:
            return json.load(response)


def _post_local(base: str, path: Path, tier: str) -> dict:
    """A local mineru-api: JSON body, local source path, markdown out.

    The box proxy takes a multipart upload and answers {markdown}; the local API takes a
    manifest and answers {files:[{content:{markdown}}]}. Same reader logic after that.
    """
    body = json.dumps({
        "files": [{"source": {"type": "local", "path": str(path)}}],
        "tier": tier,
        "output_formats": ["markdown"],
    }).encode()
    request = urllib.request.Request(
        f"{base}/v1/file_parse?wait_timeout=900",
        data=body, method="POST",
        headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=1200) as response:
        payload = json.load(response)
    markdown = "".join(
        (entry.get("content") or {}).get("markdown") or ""
        for entry in payload.get("files", [])
    )
    return {"markdown": markdown, "ok": bool(markdown)}


def _table_is_blank(block_id: str, markdown: str) -> bool:
    """True when the printed table is there but nothing is written in it.

    The row labels are printed on the card, so their presence with no values is
    a blank section, not a failed read.
    """
    grid = matrix(markdown)
    if not grid:
        return True  # no table at all: MinerU saw a blank crop
    if block_id.endswith("nerve") or block_id == "nerve_assessment":
        # A nerve table is blank only when no cell carries a mark. If a mark is
        # there but the parser produced nothing, the layout was not understood.
        return not any(
            any(mark in cell for mark in "+xX✓") for row in grid for cell in row
        )
    if block_id.endswith("def_exam"):
        labels = ("pestanejo", "lagoftalmia", "abducao", "dorsi", "olhos", "maos", "pes")
        for row in grid:
            joined = _fold(" ".join(row))
            if not any(label in joined for label in labels):
                continue
            values = [
                cell.strip() for cell in row
                if cell.strip()
                and _fold(cell) not in {"d", "e"}
                and not any(label in _fold(cell) for label in labels)
            ]
            if values:
                return False
        return True
    return True


def read_block(image_path: Path, block_id: str) -> Reading:
    """One crop in, `{"pair_id": "value"}` out, in the shape the pipeline records."""
    if block_id not in SUPPORTED_BLOCKS:
        raise ValueError(f"no table reader for block {block_id!r}")
    url = settings.MINERU_URL
    if not url and not settings.MINERU_LOCAL_BASE:
        raise RuntimeError("MINERU_URL or MINERU_LOCAL_BASE is not set")
    started = time.monotonic()
    tier = READER_TIERS[block_id]
    if settings.MINERU_LOCAL_BASE:
        payload = _post_local(settings.MINERU_LOCAL_BASE, image_path, tier)
    else:
        separator = "&" if "?" in url else "?"
        payload = _post_file(f"{url}{separator}tier={tier}", image_path, settings.MINERU_KEY)
    latency_ms = int((time.monotonic() - started) * 1000)
    markdown = payload.get("markdown") or ""
    values = READERS[block_id](markdown)
    if not values:
        # A blank table is a valid empty read: the card may have that section
        # untouched. A table that is present but produced no values is not the
        # same thing — the columns or the layout were not understood, and an
        # empty answer must not be stored as if the section were blank.
        if block_id.startswith("visit") or block_id in {
            "prednisolone", "nerve_assessment", "def_exam"
        }:
            if _table_is_blank(block_id, markdown):
                return Reading(
                    text="{}",
                    latency_ms=latency_ms,
                    usage={"reader": "mineru", "seconds": payload.get("seconds"),
                           "chars": len(markdown)},
                )
            raise RuntimeError(
                f"{block_id}: table present but not understood "
                f"({len(markdown)} chars; first 120: {markdown[:120]!r})"
            )
        raise RuntimeError(
            f"{block_id}: MinerU returned no table "
            f"({len(markdown)} chars; first 120: {markdown[:120]!r})"
        )
    # A table read that returns the wrong vocabulary is worse than a failed
    # read: it stores a confident nonsense value. Grades are 0/1/2 and nerve
    # marks are +/-, nothing else. Anything else means the parser took a header
    # row (D/E) or shifted a column — refuse it so the block falls back.
    if block_id.endswith("def_exam"):
        # Only the grade rows are 0/1/2; the eye and muscle rows are +/- and N/F/A.
        bad = {
            k: v
            for k, v in values.items()
            if ("disability_grade" in k or k.startswith("ehf_"))
            and str(v).strip() not in {"0", "1", "2"}
        }
        if bad:
            raise RuntimeError(f"{block_id}: reader returned non-grades {bad}")
    if block_id.endswith("nerve") or block_id == "nerve_assessment":
        bad = {
            k: v
            for k, v in values.items()
            if "nerve_" in k
            and str(v).strip() not in {"", "+", "-", "—", "–"}
        }
        if bad:
            raise RuntimeError(f"{block_id}: reader returned non-marks {bad}")
    return Reading(
        text=json.dumps(values, ensure_ascii=False),
        latency_ms=latency_ms,
        usage={"reader": "mineru", "seconds": payload.get("seconds"), "chars": len(markdown)},
    )
