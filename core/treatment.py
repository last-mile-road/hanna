"""Derive follow-up metrics from a 3 x 12 treatment grid."""

from __future__ import annotations

import json
from typing import Any

MONTHS = {
    "jan": 1, "janeiro": 1, "january": 1,
    "fev": 2, "fevereiro": 2, "february": 2,
    "mar": 3, "marco": 3, "março": 3, "march": 3,
    "abr": 4, "abril": 4, "april": 4,
    "mai": 5, "maio": 5, "may": 5,
    "jun": 6, "junho": 6, "june": 6,
    "jul": 7, "julho": 7, "july": 7,
    "ago": 8, "agosto": 8, "august": 8,
    "set": 9, "setembro": 9, "september": 9,
    "out": 10, "outubro": 10, "october": 10,
    "nov": 11, "novembro": 11, "november": 11,
    "dez": 12, "dezembro": 12, "december": 12,
}


def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip().lower()
        if text.isdigit():
            return int(text)
        if text in MONTHS:
            return MONTHS[text]
    return None


def _grid_object(parsed: dict[str, Any]) -> dict[str, Any]:
    grid = parsed.get("treatment_grid", parsed)
    if isinstance(grid, str):
        try:
            grid = json.loads(grid)
        except json.JSONDecodeError:
            return {}
    return grid if isinstance(grid, dict) else {}


def derive_treatment_grid(parsed: dict[str, Any]) -> dict[str, str]:
    grid = _grid_object(parsed)
    doses: list[tuple[int | None, int, int]] = []
    missed: list[tuple[int | None, int]] = []

    for row in grid.get("rows") or []:
        if not isinstance(row, dict):
            continue
        year = _int(row.get("year"))
        cells = row.get("cells")
        if isinstance(cells, list):
            for cell in cells:
                if not isinstance(cell, dict):
                    continue
                month = _int(cell.get("month"))
                status = str(cell.get("status") or "").lower()
                if status == "missed" and month is not None and 1 <= month <= 12:
                    missed.append((year, month))
                elif status == "dose":
                    day = _int(cell.get("day"))
                    if month is not None and 1 <= month <= 12 and day is not None:
                        doses.append((year, month, day))
            continue
        for dose in row.get("doses") or []:
            if not isinstance(dose, dict):
                continue
            month = _int(dose.get("month"))
            day = _int(dose.get("day"))
            if month is not None and 1 <= month <= 12 and day is not None:
                doses.append((year, month, day))
        for month in row.get("missed_months") or row.get("missed") or []:
            value = _int(month)
            if value is not None and 1 <= value <= 12:
                missed.append((year, value))

    dated = [d for d in doses if d[0] is not None]
    start = min(dated) if dated else None
    return {
        "treatment_start_date": (
            f"{start[0]:04d}-{start[1]:02d}-{start[2]:02d}" if start else ""
        ),
        "total_treatment_times": str(len(doses)),
        "treatment_months_total": str(
            len({(d[0], d[1]) for d in doses} | {(m[0], m[1]) for m in missed})
        ),
        "treatment_missed": str(len(missed)),
        "treatment_grid": json.dumps(grid, ensure_ascii=False),
    }
