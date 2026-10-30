"""v5's cut-and-stack, measured against the block prompt on the seven truth cards.

v5 cropped the gender row, split it at the M/F boundary, padded and stacked the two halves
vertically with a gap, and asked which stacked box was marked. That removes the horizontal
"which letter owns this box" question. This script repeats it with numbered bands and
compares it with the current block prompt on the same cards.

    python benchmarks/tick_stack_test.py
"""
from __future__ import annotations

import json, sys, time
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import align, settings, store
from core.db import connect
from core.inference import OpenRouter
from core.parse import parse_json_response
from core.prompts import build_prompt
from sqlalchemy import text
from benchmark_harness import GroundTruth

FORMS = ["form16","form19","form21","form22","form23","form25","form30"]
DEMO_PROFILE = "or-qwen35-9b-v1"

STACK_PROMPT = """This image shows two numbered bands stacked vertically. Each band contains one
checkbox with its printed letter beside it. Band 1 is the top band, band 2 is the bottom band.

Which band contains a handwritten tick, cross or filled mark inside its box?

Return one JSON object and nothing else:
{"ticked": 1}   if only band 1 is marked
{"ticked": 2}   if only band 2 is marked
{"ticked": null} if neither band is clearly marked, or both are
"""


def stack_gender(aligned, bbox):
    x1, y1, x2, y2 = bbox
    row = aligned[y1:y2, x1:x2]
    split = int(round(0.62 * row.shape[1]))
    left, right = row[:, :split], row[:, split:]
    width = max(left.shape[1], right.shape[1])
    def pad(part):
        out = np.full((part.shape[0], width, 3), 255, dtype=np.uint8)
        out[:, width - part.shape[1]:] = part
        return out
    gap = np.full((50, width, 3), 255, dtype=np.uint8)
    stacked = np.vstack([pad(left), gap, pad(right)])
    stacked = cv2.copyMakeBorder(stacked, 12, 12, 12, 12, cv2.BORDER_CONSTANT, value=[255,255,255])
    for number, top in ((1, 12), (2, 12 + left.shape[0] + 50)):
        cv2.putText(stacked, str(number), (16, top + 34), cv2.FONT_HERSHEY_SIMPLEX, 1.0, (0,0,0), 2)
    return stacked


def main() -> None:
    client = OpenRouter()
    with connect() as conn:
        form = conn.execute(text("select id, template_path, definition from forms order by version desc limit 1")).mappings().one()
        blocks, pairs, concepts = store.form_rows(conn, form["id"])
        profiles = {r["id"]: r for r in conn.execute(text("select * from profiles")).mappings()}
    demo = next(b for b in blocks if b.id == "general_demographic")
    gender_pair = next(p for p in demo.pairs if p.id == "gender")
    if gender_pair.bbox is None:
        import yaml
        layout = yaml.safe_load(
            (settings.COUNTRIES_DIR / "mozambique" / "validation.yaml").read_text())
        gender_pair.bbox = layout["required"]["gender"]["bbox"]
    profile = profiles[DEMO_PROFILE]
    print("gender bbox:", gender_pair.bbox, "| profile:", DEMO_PROFILE)

    results = []
    for form_name in FORMS:
        truth = GroundTruth.from_json(
            settings.COUNTRIES_DIR / "mozambique" / "ground_truth" / f"{form_name}.json",
            settings.IMAGES_DIR).expected
        expected = (truth.get("gender") or "").upper()
        image = Path(settings.IMAGES_DIR) / f"{form_name}.jpg"
        alignment = align.align_card(image, Path(form["template_path"]))

        # A: the current block prompt
        crop = align.crop_block(alignment.image, demo.bbox)
        tmp = Path("/home/sam/tmp/opencode") / f"{form_name}_demo.jpg"
        align.save_jpeg(crop, tmp)
        prompt = build_prompt(demo, demo.pairs, concepts, country="Mozambique", language="Portuguese")
        a = client.complete(prompt, [tmp], profile["model"], profile["settings"])
        a_parsed = parse_json_response(a.text, ["gender"])
        a_value = str(a_parsed.get("gender") or "").upper()[:1]

        # B: cut-and-stack with numbered bands
        stacked = stack_gender(alignment.image, gender_pair.bbox)
        tmp_b = Path("/home/sam/tmp/opencode") / f"{form_name}_gender_stack.jpg"
        align.save_jpeg(stacked, tmp_b)
        b = client.complete(STACK_PROMPT, [tmp_b], profile["model"], profile["settings"])
        b_parsed = parse_json_response(b.text, ["ticked"])
        ticked = b_parsed.get("ticked")
        b_value = {1: "M", 2: "F"}.get(ticked if isinstance(ticked, int) else None, "")

        results.append({"form": form_name, "expected": expected,
                        "block": a_value, "block_ok": a_value == expected,
                        "stack": b_value, "stack_ok": b_value == expected,
                        "stack_raw": ticked})
        print(f"{form_name:7s} truth={expected}  block={a_value or '?':1s} {'ok' if a_value==expected else 'X'}   "
              f"stack={b_value or '?':1s} {'ok' if b_value==expected else 'X'}  ({ticked})", flush=True)

    Path("output/tick_stack_test.json").write_text(json.dumps(results, indent=2))
    b_ok = sum(r["block_ok"] for r in results)
    s_ok = sum(r["stack_ok"] for r in results)
    print(f"\nblock prompt {b_ok}/{len(results)} | cut-and-stack {s_ok}/{len(results)}")


if __name__ == "__main__":
    main()
