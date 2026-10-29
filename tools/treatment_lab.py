"""Treatment-grid lab: current prompt vs explicit headers vs a 12-cell schema.

Usage:
  python tools/treatment_lab.py --strategies current,headers,cells --models 8b,9b
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import normalise, settings, store
from core.db import connect
from core.forms import load_form_pack
from core.inference import OpenRouter
from core.parse import parse_json_response
from core.prompts import build_prompt
from core.schema import block_runs, cards, forms, profiles, runs
from core.treatment import derive_treatment_grid

PURPOSES = {
    "form16": "dose-grid",
    "form19": "form19-8b-dg",
    "form21": "form21-8b",
    "form22": "form22-8b",
    "form23": "form23-8b",
    "form25": "form25-8b",
    "form30": "form30-8b",
}

RULES = """Column headers: Janeiro = 1, Fevereiro = 2, Março = 3, Abril = 4, Maio = 5,
Junho = 6, Julho = 7, Agosto = 8, Setembro = 9, Outubro = 10, Novembro = 11,
Dezembro = 12. The month is the column header, not a second number in the cell.
A cell like 2/5 means day 2 in May; never concatenate 2 and 5 into 21.
A capital F means the patient missed that month. A whole number 1-31 is a dose day.
The Rubrica/Faltou line is a signature, not a digit."""

CELLS_PROMPT = """You are reading the treatment grid of a Mozambique leprosy clinic card.

The grid has three rows, one per course year. Each row has the year on the left,
then twelve month columns from Janeiro to Dezembro.

Return one JSON object with all twelve months for every row:
{{"treatment_grid": {{"rows": [
  {{"year": 2022, "cells": [
    {{"month": 1, "status": "empty"}},
    {{"month": 2, "status": "dose", "day": 18}},
    {{"month": 6, "status": "missed"}},
    ...
  ]}}
]}}}}

For each cell use status "dose" with a day 1-31 when a day number is written,
"missed" only when a clear capital F is written in the cell, and "empty" otherwise.
A blank cell, a signature, or the Rubrica/Faltou line is "empty", not "missed".
Do not infer a missed month from a blank cell. Return all twelve cells per row, in
month order.

{rules}
Return JSON only, no markdown. The handwriting is Portuguese.
"""


def load_case(card_name: str):
    with connect() as conn:
        card = conn.execute(
            select(cards).where(cards.c.image_path.like(f"%/{card_name}.jpg"))
        ).mappings().one()
        form = conn.execute(select(forms).order_by(forms.c.version.desc()).limit(1)).mappings().one()
        blocks, pairs, concepts = store.form_rows(conn, form["id"])
        truth = store.ground_truth_map(conn, card["id"], version=1)
        run = conn.execute(
            select(block_runs.c.run_id, block_runs.c.crop_path)
            .join(runs, runs.c.id == block_runs.c.run_id)
            .where(block_runs.c.block_id == "treatment_follow")
            .where(runs.c.card_id == card["id"])
            .order_by(block_runs.c.id.desc())
            .limit(1)
        ).mappings().one()
    block = next(b for b in blocks if b.id == "treatment_follow")
    pairs = [p for p in pairs if p.block_id == "treatment_follow"]
    return block, pairs, concepts, truth, Path(run["crop_path"])


def score(case, values):
    block, pairs, concepts, truth, _ = case
    judged = correct = 0
    misses = []
    for pair in pairs:
        row = truth.get(pair.id)
        if not row:
            continue
        result = normalise.compare(
            pair.value_type, values.get(pair.id), row["value"], concepts[pair.concept]
        )
        if result is None:
            continue
        judged += 1
        if result:
            correct += 1
        else:
            misses.append(f"{pair.id}: {values.get(pair.id)!r} != {row['value']!r}")
    return correct, judged, misses


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="current,headers,cells")
    ap.add_argument("--models", default="8b,9b")
    ap.add_argument("--cards", default="form16,form19,form21,form22,form23,form25,form30")
    args = ap.parse_args()

    model_ids = {
        "8b": "or-qwen3vl-8b-v1",
        "9b": "or-qwen35-9b-v1",
        "27b": "or-qwen35-27b-low",
    }
    with connect() as conn:
        profile_rows = {r["id"]: r for r in conn.execute(select(profiles)).mappings()}

    client = OpenRouter()
    out_dir = settings.OUTPUT_DIR / "treatment_lab"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []

    for card_name in args.cards.split(","):
        case = load_case(card_name)
        block, pairs, concepts, truth, crop_path = case
        print(f"\n== {card_name}")
        for model_key in args.models.split(","):
            profile = profile_rows[model_ids[model_key]]
            for strategy in args.strategies.split(","):
                try:
                    if strategy == "current":
                        prompt = build_prompt(block, pairs, concepts)
                    elif strategy == "headers":
                        prompt = build_prompt(block, pairs, concepts).replace(
                            "Return one JSON object", RULES + "\n\nReturn one JSON object"
                        )
                    elif strategy == "cells":
                        prompt = CELLS_PROMPT.format(rules=RULES)
                    else:
                        continue

                    completion = client.complete(
                        prompt, [crop_path], profile["model"], profile["settings"]
                    )
                    parsed = parse_json_response(completion.text)
                    derived = derive_treatment_grid(parsed)
                    values = {p.id: derived.get(p.id) for p in pairs}
                    correct, judged, misses = score(case, values)
                    results.append(
                        {
                            "card": card_name,
                            "model": model_key,
                            "strategy": strategy,
                            "correct": correct,
                            "judged": judged,
                            "misses": misses,
                            "raw": completion.text,
                            "values": values,
                            "latency_ms": completion.latency_ms,
                            "cost": completion.cost_usd,
                        }
                    )
                    print(
                        f"  {model_key:3s} {strategy:8s} {correct}/{judged} "
                        f"{completion.latency_ms:6d} ms | " + "; ".join(misses)
                    )
                except Exception as exc:
                    print(f"  {model_key:3s} {strategy:8s} ERROR {exc}")

    path = out_dir / "results.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nwritten {path}")

    print("\nSUMMARY")
    for strategy in args.strategies.split(","):
        for model_key in args.models.split(","):
            rows = [r for r in results if r["strategy"] == strategy and r["model"] == model_key]
            correct = sum(r["correct"] for r in rows)
            judged = sum(r["judged"] for r in rows)
            print(f"  {model_key:3s} {strategy:8s} {correct}/{judged}")


if __name__ == "__main__":
    main()
