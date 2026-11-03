"""Test lfm3-vl (local, Ollama) on the hardest cards, block by block.

Same crops, same prompts, same parser, same scoring as `core.run`: the only change is the
backend. That makes the per-block numbers directly comparable with the ledger.

    python benchmarks/lfm_compare.py --forms form21,form22,form30
    python benchmarks/lfm_compare.py --rescore      # reuse saved responses
"""

from __future__ import annotations

import argparse
import base64
import json
import sys
import time
import urllib.request
from datetime import date
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import align, normalise, settings  # noqa: E402
from core.db import connect  # noqa: E402
from core.forms import load_concepts, load_form_pack  # noqa: E402
from core.parse import parse_json_response  # noqa: E402
from core.prompts import build_prompt  # noqa: E402
from core.treatment import derive_treatment_grid  # noqa: E402
from benchmark_harness import GroundTruth  # noqa: E402

OLLAMA_URL = "http://127.0.0.1:11434/api/chat"
OCR_BLOCK_TYPES = {"value_pairs", "tick_grid", "annotated_table", "free_text"}
OUT = ROOT / "output" / "lfm"


def ask_ollama(model: str, prompt: str, image: Path, num_predict: int = 1200) -> tuple[str, float]:
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": prompt,
                      "images": [base64.b64encode(image.read_bytes()).decode()]}],
        "stream": False,
        "think": False,
        "options": {"temperature": 0, "num_predict": num_predict},
    }
    req = urllib.request.Request(OLLAMA_URL, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json"})
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=900) as response:
        body = json.load(response)
    return body["message"]["content"], time.monotonic() - started


def latest_route(form: str) -> tuple[str | None, dict[str, bool], dict[str, str]]:
    """Latest completed routed run: pair -> correct, block -> profile."""
    with connect() as conn:
        run_id = conn.execute(
            text("""SELECT r.id FROM runs r JOIN cards c ON c.id = r.card_id
                     WHERE c.image_path LIKE :like AND r.status = 'done'
                       AND r.purpose NOT LIKE 'lfm%'
                     ORDER BY r.started_at DESC LIMIT 1"""),
            {"like": f"%/{form}.jpg"},
        ).scalar()
        if run_id is None:
            return None, {}, {}
        rows = conn.execute(
            text("""SELECT o.pair_id, o.correct, br.profile_id
                      FROM observations o JOIN block_runs br ON br.id = o.block_run_id
                     WHERE o.run_id = :run AND o.correct IS NOT NULL"""),
            {"run": run_id},
        ).mappings()
        correct, profile = {}, {}
        for row in rows:
            correct[row["pair_id"]] = bool(row["correct"])
            profile[row["pair_id"]] = row["profile_id"]
        return str(run_id), correct, profile


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default="form21,form22,form30")
    ap.add_argument("--model", default="lfm3-vl:2b-q8_0")
    ap.add_argument("--blocks", default="")
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--out", default=str(OUT / "lfm_compare.json"))
    args = ap.parse_args()

    pack = load_form_pack("mozambique")
    concepts = load_concepts()
    template = settings.COUNTRIES_DIR / "mozambique" / "template.jpg"
    reference_year = 2026
    selected_blocks = [b for b in pack.blocks if b.pairs and (
        not args.blocks or b.id in args.blocks.split(","))]
    report = []

    for form in args.forms.split(","):
        image = settings.IMAGES_DIR / f"{form}.jpg"
        truth = GroundTruth.from_json(
            settings.COUNTRIES_DIR / "mozambique" / "ground_truth" / f"{form}.json",
            settings.IMAGES_DIR).expected
        pair_concept = {p.id: p.concept for b in pack.blocks for p in b.pairs}
        truth_by_pair = {pid: truth[c] for pid, c in pair_concept.items() if c in truth}

        run_id, baseline, baseline_profile = latest_route(form)
        aligned = align.align_card(image, template)
        form_dir = OUT / form
        form_dir.mkdir(parents=True, exist_ok=True)

        for block in selected_blocks:
            crop = align.crop_block(aligned.image, block.bbox)
            send = align.ocr_preprocess(crop) if block.block_type in OCR_BLOCK_TYPES else crop
            image_path = form_dir / f"{block.id}.jpg"
            align.save_jpeg(send, image_path)
            prompt = build_prompt(block, block.pairs, concepts, country="Mozambique",
                                  language="Portuguese", with_template=False)
            cache = form_dir / f"{block.id}.{args.model.replace(':', '_')}.txt"

            if args.rescore and cache.exists():
                raw_text, seconds = cache.read_text(), 0.0
            else:
                raw_text, seconds = ask_ollama(args.model, prompt, image_path)
                cache.write_text(raw_text)

            parsed = parse_json_response(raw_text, [p.id for p in block.pairs])
            if block.block_type == "dose_grid" and parsed:
                parsed.update(derive_treatment_grid(parsed))

            rows = []
            for pair in block.pairs:
                concept = concepts[pair.concept]
                expected = truth_by_pair.get(pair.id)
                if expected is None:
                    continue
                got = normalise.canonical(pair.value_type, parsed.get(pair.id), concept,
                                          reference_year=reference_year)
                correct = normalise.compare(pair.value_type, got, expected, concept)
                rows.append({"pair": pair.id, "expected": expected, "value": got,
                             "correct": bool(correct),
                             "pipeline": baseline.get(pair.id),
                             "pipeline_profile": baseline_profile.get(pair.id)})

            judged = len(rows)
            hit = sum(r["correct"] for r in rows)
            report.append({"form": form, "block": block.id, "type": block.block_type,
                           "seconds": round(seconds, 2), "judged": judged, "correct": hit,
                           "parsed": len(parsed), "raw_chars": len(raw_text),
                           "baseline_run": run_id, "rows": rows})
            print(f"  {form} {block.id:22s} {hit}/{judged}  {seconds:5.1f}s  "
                  f"parsed {len(parsed)}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
