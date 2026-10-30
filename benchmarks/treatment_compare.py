"""Derive treatment fields from PaddleOCR-VL's HTML grid.

The grid is a table like any other: one row per year with a `Dia` row and a
`Rubrica/Faltou` row, one column per month. Reading order alone loses the column, so the
parser aligns cells to the header row and derives start date, doses, months and missed.

    python benchmarks/treatment_compare.py
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "benchmarks"))
sys.path.insert(0, str(ROOT))

from table_compare import table_matrix  # noqa: E402
from benchmark_harness import GroundTruth  # noqa: E402
from core import settings  # noqa: E402

MONTHS = ["janeiro", "fevereiro", "março", "marco", "abril", "maio", "malo", "junho",
          "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
MONTH_NUMBER = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
                "maio": 5, "malo": 5, "junho": 6, "julho": 7, "agosto": 8,
                "setembro": 9, "outubro": 10, "novembro": 11, "dezembro": 12}
FORMS = ["form16", "form19", "form21", "form22", "form23", "form25", "form30"]


def parse_year(cells: list[str]) -> int | None:
    raw = "".join(cells)
    found = re.findall(r"20\d{2}|20[.\s]*\d{2}", raw)
    if not found:
        return None
    digits = re.sub(r"\D", "", found[0])
    if len(digits) == 4:
        return int(digits)
    if len(digits) == 2:
        return 2000 + int(digits)
    return None


def derive(matrix: list[list[str]]) -> dict:
    header_index = None
    for index, row in enumerate(matrix):
        lowered = [cell.lower() for cell in row]
        if sum(1 for cell in lowered if cell in MONTHS) >= 6:
            header_index = index
            header = row
            break
    if header is None:
        return {}
    month_at = {}
    for index, cell in enumerate(header):
        key = cell.strip().lower()
        if key in MONTH_NUMBER:
            month_at[index] = MONTH_NUMBER[key]    # The year is sometimes in the row above the month header, sometimes split across
    # the first cells of the data row ("20..", "26").
    header_year = parse_year(header)
    if header_year is None:
        for row in matrix[:header_index]:
            header_year = header_year or parse_year(row)

    doses: list[tuple[int, int, int | None]] = []
    missed = 0
    for row in matrix:
        lowered = [cell.strip().lower() for cell in row]
        if "dia" not in lowered:
            continue
        dia_index = lowered.index("dia")
        year = parse_year(row[:dia_index]) or header_year
        if year is None:
            continue
        month_cells = row[dia_index + 1:]
        leading = [cell for cell in row[:dia_index] if cell.strip()]
        # A dropped empty cell shifts the row left and the filled months sit against the
        # right edge, so pad the left when the row is short. A split year ("20..", "26")
        # adds a cell instead and must not be padded.
        if len(leading) <= 1 and len(month_cells) < 12:
            month_cells = [""] * (12 - len(month_cells)) + month_cells
        for offset, value in enumerate(month_cells[:12]):
            month = offset + 1
            value = value.strip()
            if re.fullmatch(r"F", value, re.I):
                missed += 1
            elif re.search(r"\d", value):
                day_match = re.match(r"(\d{1,2})\s*/\s*(\d{1,2})", value)
                bare_day = re.fullmatch(r"(\d{1,2})", value)
                day = int(day_match.group(1)) if day_match else (
                    int(bare_day.group(1)) if bare_day else None)
                cell_month = int(day_match.group(2)) if day_match else month
                doses.append((year, cell_month, day))
    if not doses:
        return {"doses": 0, "months": missed, "missed": missed}
    doses.sort(key=lambda item: (item[0], item[1]))
    year, month, day = doses[0]
    return {
        "treatment_start_date": f"{year:04d}-{month:02d}-{day:02d}" if day else None,
        "doses": len(doses),
        "months": len({(y, m) for y, m, _ in doses}) + missed,
        "missed": missed,
    }


def main() -> None:
    rows = []
    for form in FORMS:
        truth = GroundTruth.from_json(
            settings.COUNTRIES_DIR / "mozambique" / "ground_truth" / f"{form}.json",
            settings.IMAGES_DIR,
        ).expected
        path = Path(f"/home/sam/tmp/opencode/standard/paddle/{form}__treatment_follow.html")
        matrix = table_matrix(path.read_text()) if path.exists() else []
        derived = derive(matrix)
        expected = {
            "treatment_start_date": truth.get("treatment_start_date"),
            "doses": truth.get("treatment_doses_total"),
            "months": truth.get("treatment_months_total"),
            "missed": truth.get("treatment_missed"),
        }
        checks = {
            key: (derived.get(key) is not None
                  and str(derived.get(key)).lstrip("0") == str(expected[key]).lstrip("0"))
            for key in expected if expected[key] is not None
        }
        rows.append({"form": form, "derived": derived, "expected": expected,
                     "checks": checks, "table_found": bool(matrix)})
        print(f"{form}: {derived} expected {expected} -> "
              f"{sum(checks.values())}/{len(checks)}")
    Path(ROOT / "output" / "treatment_compare.json").write_text(
        json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
