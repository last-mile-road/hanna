"""Hand/feet lab: compare counting strategies on the same seven crops.

Strategies:
  fields  - the current one-prompt, four-count block
  legend  - current prompt plus explicit D/E, X-only and ulcer rules
  halves  - crop the D and E halves separately; the code assigns the side

Usage:
  python tools/hand_feet_lab.py --strategies fields,legend,halves --models 8b,9b
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import align, normalise, settings, store
from core.db import connect
from core.forms import load_form_pack
from core.inference import OpenRouter
from core.parse import parse_json_response
from core.prompts import build_prompt
from core.schema import block_runs, cards, forms, profiles, runs

LEGEND = """
Rules for this diagram:
- The left half is column D = the patient's right; the right half is column E = the patient's left.
- An X marks an insensitive point. A tick, a dot or a printed mark is NOT an X.
- A filled black circle is an ulcer, not an insensitive point.
- A blacked-out or amputated digit is a complication, not an X.
- Count only X marks, separately for the hand and the foot.
"""

HALF_PROMPT = """This is the {side_name} half (column {column}) of the hand and foot
sensitivity test on a Mozambique leprosy card. It shows one hand and one foot.

Count the X marks on the hand drawing and on the foot drawing. A tick, a dot or a
printed test point is NOT an X. A filled black circle is an ulcer, not an X.

Return one JSON object and nothing else:
{{"hand": <whole number>, "foot": <whole number>}}
"""

PURPOSES = {
    "form16": "dose-grid",
    "form19": "form19-8b-dg",
    "form21": "form21-8b",
    "form22": "form22-8b",
    "form23": "form23-8b",
    "form25": "form25-8b",
    "form30": "form30-8b",
}


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
            .where(block_runs.c.block_id == "hand_feet_assessment")
            .where(runs.c.card_id == card["id"])
            .order_by(block_runs.c.id.desc())
            .limit(1)
        ).mappings().one()
    block = next(b for b in blocks if b.id == "hand_feet_assessment")
    pairs = [p for p in pairs if p.block_id == "hand_feet_assessment"]
    return block, pairs, concepts, truth, Path(run["crop_path"])


def score(case, values: dict[str, str]):
    block, pairs, concepts, truth, _ = case
    judged = correct = 0
    misses = []
    for pair in pairs:
        row = truth.get(pair.id)
        if not row:
            continue
        expected = row["value"]
        concept = concepts[pair.concept]
        result = normalise.compare(pair.value_type, values.get(pair.id), expected, concept)
        if result is None:
            continue
        judged += 1
        if result:
            correct += 1
        else:
            misses.append(f"{pair.id}: {values.get(pair.id)!r} != {expected!r}")
    return correct, judged, misses


def map_fields(parsed, pair_ids):
    return {k: v for k, v in parsed.items() if k in pair_ids}


def map_half(parsed, side: str):
    hand = parsed.get("hand") or parsed.get("Hand")
    foot = parsed.get("foot") or parsed.get("Foot")
    prefix = "right" if side == "D" else "left"
    return {
        f"insensitive_points_{prefix}_hand": hand,
        f"insensitive_points_{prefix}_foot": foot,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="fields,legend,halves")
    ap.add_argument("--models", default="8b,9b")
    ap.add_argument("--cards", default="form16,form19,form21,form22,form23,form25,form30")
    ap.add_argument("--upscale", type=int, default=1)
    args = ap.parse_args()

    model_ids = {
        "8b": "or-qwen3vl-8b-v1",
        "9b": "or-qwen35-9b-v1",
        "27b": "or-qwen35-27b-low",
    }
    with connect() as conn:
        profile_rows = {r["id"]: r for r in conn.execute(select(profiles)).mappings()}

    out_dir = settings.OUTPUT_DIR / "hand_feet_lab"
    out_dir.mkdir(parents=True, exist_ok=True)
    client = OpenRouter()
    results = []

    for card_name in args.cards.split(","):
        case = load_case(card_name)
        block, pairs, concepts, truth, crop_path = case
        image = cv2.imread(str(crop_path))
        print(f"\n== {card_name} | {crop_path.name}")
        pair_ids = [p.id for p in pairs]
        h, w = image.shape[:2]

        for model_key in args.models.split(","):
            profile = profile_rows[model_ids[model_key]]
            for strategy in args.strategies.split(","):
                try:
                    values = {}
                    raw_parts = []
                    latency = 0
                    cost = 0.0

                    if strategy in {"fields", "legend"}:
                        prompt = build_prompt(block, pairs, concepts)
                        if strategy == "legend":
                            prompt = prompt.replace(
                                "This crop contains a body diagram or a drawing.",
                                LEGEND + "\nThis crop contains a body diagram or a drawing.",
                            )
                        completion = client.complete(
                            prompt, [crop_path], profile["model"], profile["settings"]
                        )
                        parsed = parse_json_response(completion.text, pair_ids)
                        values = map_fields(parsed, pair_ids)
                        raw_parts.append(completion.text)
                        latency += completion.latency_ms
                        cost += completion.cost_usd or 0
                    elif strategy == "halves":
                        halves = {
                            "D": image[:, : w // 2 + 10],
                            "E": image[:, w // 2 - 10 :],
                        }
                        for side, half in halves.items():
                            if args.upscale > 1:
                                half = cv2.resize(
                                    half,
                                    None,
                                    fx=args.upscale,
                                    fy=args.upscale,
                                    interpolation=cv2.INTER_CUBIC,
                                )
                            path = out_dir / f"{card_name}_{model_key}_{side}_{args.upscale}x.jpg"
                            align.save_jpeg(half, path)
                            prompt = HALF_PROMPT.format(
                                side_name="left" if side == "D" else "right",
                                column=side,
                            )
                            completion = client.complete(
                                prompt, [path], profile["model"], profile["settings"]
                            )
                            parsed = parse_json_response(completion.text)
                            values.update(map_half(parsed, side))
                            raw_parts.append(completion.text)
                            latency += completion.latency_ms
                            cost += completion.cost_usd or 0
                    else:
                        continue

                    correct, judged, misses = score(case, values)
                    results.append(
                        {
                            "card": card_name,
                            "model": model_key,
                            "strategy": strategy,
                            "upscale": args.upscale,
                            "correct": correct,
                            "judged": judged,
                            "misses": misses,
                            "values": values,
                            "raw": "\n---\n".join(raw_parts),
                            "latency_ms": latency,
                            "cost": cost,
                        }
                    )
                    print(
                        f"  {model_key:3s} {strategy:7s} {correct}/{judged} "
                        f"{latency:6d} ms | " + "; ".join(misses)
                    )
                except Exception as exc:
                    print(f"  {model_key:3s} {strategy:7s} ERROR {exc}")

    path = out_dir / f"results_{args.upscale}x.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nwritten {path}")

    print("\nSUMMARY")
    for strategy in args.strategies.split(","):
        for model_key in args.models.split(","):
            rows = [r for r in results if r["strategy"] == strategy and r["model"] == model_key]
            correct = sum(r["correct"] for r in rows)
            judged = sum(r["judged"] for r in rows)
            print(f"  {model_key:3s} {strategy:7s} {correct}/{judged}")


if __name__ == "__main__":
    main()
