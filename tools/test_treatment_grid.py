"""Experiment: can a VLM read the 3 x 12 treatment grid?

Standalone tool, no pipeline changes. It crops the treatment block from an
aligned form16, asks for the grid as JSON, and derives the follow-up metrics.
"""

from __future__ import annotations

import argparse
import json
import sys

import cv2
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import select

from core import align, settings
from core.db import connect
from core.inference import OpenRouter
from core.parse import parse_json_response
from core.schema import profiles

PROMPT = """You are reading the treatment grid of a Mozambique leprosy clinic card.

The grid has three rows, one per course year. Each row has the year written on
the left, then twelve month columns from Janeiro to Dezembro. Under the day
number is a signature line (Rubrica/Faltou).

For each row read:
- the year in the left column
- every month cell that contains a day number (the day the supervised blister
  pack was given); a signature or mark under the day confirms the dose
- a large F written in a month cell means the patient missed that month

Return JSON only, in this shape:
{"rows": [{"year": 2025, "doses": [{"month": 5, "day": 31}], "missed_months": []}]}

Use month numbers 1-12. Omit empty months. Use null for an empty row. Do not
invent doses. If a date has no year, use null for the year.
"""


def derive(grid: dict) -> dict:
    doses = []
    missed = []
    for row in grid.get("rows") or []:
        if not isinstance(row, dict):
            continue
        year = row.get("year")
        for dose in row.get("doses") or []:
            month = dose.get("month")
            day = dose.get("day")
            if isinstance(month, int) and 1 <= month <= 12:
                doses.append((year, month, day))
        for month in row.get("missed_months") or []:
            if isinstance(month, int) and 1 <= month <= 12:
                missed.append((year, month))
    dated = [d for d in doses if isinstance(d[0], int) and isinstance(d[2], int)]
    start = min(dated) if dated else None
    return {
        "start_date": f"{start[0]:04d}-{start[1]:02d}-{start[2]:02d}" if start else None,
        "doses_total": len(doses),
        "months_total": len({(d[0], d[1]) for d in doses}),
        "missed_total": len(missed),
        "doses": doses,
        "missed": missed,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="or-qwen35-27b-low")
    ap.add_argument("--aligned", default="output/runs/9ea4e14e-ce82-4771-820d-715af8de805c/aligned.jpg")
    args = ap.parse_args()

    image = align.crop_block(cv2.imread(args.aligned), [52, 1976, 2713, 2437])
    out = settings.OUTPUT_DIR / "verify" / "treatment_grid.jpg"
    align.save_jpeg(image, out)

    with connect() as c:
        profile = c.execute(select(profiles).where(profiles.c.id == args.profile)).mappings().one()

    client = OpenRouter()
    completion = client.complete(PROMPT, [out], profile["model"], profile["settings"])
    grid = parse_json_response(completion.text)

    print("profile:", args.profile, "| model:", profile["model"])
    print("latency:", completion.latency_ms, "ms | tokens:", completion.input_tokens, completion.output_tokens, "| cost:", completion.cost_usd)
    print("raw:", completion.text[:1200])
    derived = derive(grid)
    print("grid:", json.dumps(grid, ensure_ascii=False))
    print("derived:", json.dumps(derived, ensure_ascii=False))
    print("truth: start 2025-05-31 | doses 1 | months 1 | missed 0")


if __name__ == "__main__":
    main()
