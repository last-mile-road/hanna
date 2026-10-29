"""Resolution sweep: how little source resolution can the pipeline survive?

Phase 0 measurement, 2026-10-29. Deliberately outside the run ledger: these are
preprocessing probes, not extractions of record, and mixing them into `runs` would
pollute the accuracy views. The script reuses the production path (align, crop,
prompt, routing, parse, normalise, compare) and scores against the country's
ground-truth sheets.

    python benchmarks/resolution_sweep.py                       # all 7 sheets
    python benchmarks/resolution_sweep.py --sizes orig,2400,1200
    python benchmarks/resolution_sweep.py --forms form16 --sizes 1600

The alignment warps every source onto the template (3332x2502), so the model always
sees template-scale crops. What changes with source resolution is the information
available to SIFT and to the warp, which is what this measures.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import cv2
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import align, normalise, settings, store  # noqa: E402
from core.db import connect  # noqa: E402
from core.inference import OpenRouter  # noqa: E402
from core.parse import parse_json_response  # noqa: E402
from core.prompts import build_prompt, prompt_hash  # noqa: E402
from core.run import OCR_BLOCK_TYPES, _find_form, _profiles, _routing  # noqa: E402
from core.treatment import derive_treatment_grid  # noqa: E402

sys.path.insert(0, str(ROOT / "benchmarks"))
from benchmark_harness import GroundTruth  # noqa: E402

DEFAULT_FORMS = ["form16", "form19", "form21", "form22", "form23", "form25", "form30"]
DEFAULT_SIZES = ["orig", 3332, 2400, 1600, 1200]


def prepare_image(src: Path, size: str | int, out_dir: Path) -> tuple[Path, int]:
    """Return the image path to use and its long edge."""
    image = cv2.imread(str(src))
    if image is None:
        raise FileNotFoundError(src)
    height, width = image.shape[:2]
    long_edge = max(height, width)
    if size == "orig":
        return src, long_edge
    size = int(size)
    if long_edge <= size:
        return src, long_edge
    scale = size / long_edge
    resized = cv2.resize(
        image, (round(width * scale), round(height * scale)), interpolation=cv2.INTER_AREA
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{src.stem}_{size}.jpg"
    ok = cv2.imwrite(str(path), resized, [cv2.IMWRITE_JPEG_QUALITY, 92])
    if not ok:
        raise OSError(f"could not write {path}")
    return path, max(resized.shape[:2])


def run_one(form: str, size: str | int, out_root: Path) -> dict:
    source = settings.IMAGES_DIR / f"{form}.jpg"
    if not source.exists():
        raise FileNotFoundError(source)
    truth_sheet = settings.COUNTRIES_DIR / "mozambique" / "ground_truth" / f"{form}.json"
    # The sheet is keyed by concept, the run by pair id: map through the pair's concept.
    truth_by_concept = GroundTruth.from_json(truth_sheet, settings.IMAGES_DIR).expected

    image_path, long_edge = prepare_image(source, size, out_root / "images")

    with connect() as conn:
        form_row = _find_form(conn)
        profiles = _profiles(conn)
        routing_map = _routing(form_row, "or-qwen3vl-8b-v1")
        blocks, pairs, concepts = store.form_rows(conn, form_row["id"])

    template = Path(form_row["template_path"])
    started = time.monotonic()
    try:
        alignment = align.align_card(image_path, template)
    except Exception as exc:  # alignment failure is a result, not a crash
        return {
            "form": form, "size": size, "long_edge": long_edge,
            "status": "align_failed", "error": str(exc),
            "good_matches": 0, "judged": 0, "correct": 0, "blocks": [],
        }
    alignment_ms = int((time.monotonic() - started) * 1000)

    selected = [b for b in blocks if b.pairs]
    prepared = []
    for block in selected:
        profile = profiles[routing_map["blocks"].get(block.id, routing_map["default"])]
        crop = align.crop_block(alignment.image, block.bbox)
        send = crop
        if block.block_type in OCR_BLOCK_TYPES:
            send = align.ocr_preprocess(crop)
        upscale = int((profile["settings"] or {}).get("upscale", 1))
        if upscale > 1:
            send = cv2.resize(send, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC)
        tmp = out_root / "crops" / f"{form}_{size}_{block.id}.jpg"
        align.save_jpeg(send, tmp)
        paths = [tmp]
        with_template = (
            profile["crop_strategy"] == "block_plus_template"
            and block.block_type == "visual_annotation"
        )
        if with_template:
            tpl_crop = align.crop_block(alignment.template, block.bbox)
            tpl_path = out_root / "crops" / f"{form}_{size}_{block.id}_tpl.jpg"
            align.save_jpeg(tpl_crop, tpl_path)
            paths.append(tpl_path)
        prompt = build_prompt(
            block, block.pairs, concepts,
            country=form_row["definition"].get("country", "Mozambique").title(),
            language=form_row["definition"].get("language", "Portuguese"),
            with_template=with_template,
        )
        prepared.append({
            "block": block, "profile": profile, "paths": paths, "prompt": prompt,
            "hash": prompt_hash(prompt, profile["model"], profile["settings"]),
            "completion": None, "error": None,
        })

    client = OpenRouter()
    groups: dict[str, list[dict]] = {}
    for item in prepared:
        groups.setdefault(item["profile"]["id"], []).append(item)
    for items in groups.values():
        profile = items[0]["profile"]
        workers = max(1, min(int((profile["settings"] or {}).get("parallel", 4)), len(items)))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {
                pool.submit(
                    client.complete, item["prompt"], item["paths"],
                    profile["model"], profile["settings"],
                ): item
                for item in items
            }
            for future in as_completed(futures):
                item = futures[future]
                try:
                    item["completion"] = future.result()
                except Exception as exc:
                    item["error"] = str(exc)

    judged = correct = 0
    per_block = []
    for item in prepared:
        block = item["block"]
        parsed = {}
        if item["completion"] is not None:
            parsed = parse_json_response(
                item["completion"].text, [p.id for p in block.pairs]
            )
            if block.block_type == "dose_grid" and parsed:
                parsed.update(derive_treatment_grid(parsed))
        block_judged = block_correct = 0
        for pair in block.pairs:
            expected = truth_by_concept.get(pair.concept)
            if expected is None:
                continue
            got = normalise.canonical(pair.value_type, parsed.get(pair.id), concepts[pair.concept])
            comparison = normalise.compare(
                pair.value_type, got, expected, concepts[pair.concept]
            )
            if comparison is None:  # not comparable, same rule as core.run
                continue
            block_judged += 1
            block_correct += int(comparison)
        judged += block_judged
        correct += block_correct
        per_block.append({
            "block": block.id, "status": "done" if item["completion"] else "failed",
            "judged": block_judged, "correct": block_correct,
            "latency_ms": getattr(item["completion"], "latency_ms", None),
            "error": item["error"],
        })

    return {
        "form": form, "size": size, "long_edge": long_edge,
        "status": "done", "good_matches": alignment.good_matches,
        "alignment_ms": alignment_ms, "judged": judged, "correct": correct,
        "accuracy": round(correct / judged, 4) if judged else None,
        "blocks": per_block,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default=",".join(DEFAULT_FORMS))
    ap.add_argument("--sizes", default=",".join(str(s) for s in DEFAULT_SIZES))
    ap.add_argument("--out", default=str(ROOT / "output" / "resolution_sweep"))
    args = ap.parse_args()

    forms = [f.strip() for f in args.forms.split(",") if f.strip()]
    sizes = [s.strip() for s in args.sizes.split(",") if s.strip()]
    out_root = Path(args.out)
    out_root.mkdir(parents=True, exist_ok=True)

    results: list[dict] = []
    json_path = out_root / "results.json"
    for form in forms:
        for size in sizes:
            try:
                row = run_one(form, size, out_root)
            except Exception as exc:  # keep the sweep alive
                row = {"form": form, "size": size, "status": "error", "error": str(exc)}
            results.append(row)
            json_path.write_text(json.dumps(results, indent=2, default=str))
            acc = row.get("accuracy")
            print(
                f"{form:8s} {str(size):5s} {row.get('status','?'):11s} "
                f"{row.get('correct',0)}/{row.get('judged',0)} "
                f"{(acc if acc is not None else '-')!s:6s} "
                f"matches={row.get('good_matches', '-')}",
                flush=True,
            )

    print("\nform     " + "  ".join(f"{str(s):>10s}" for s in sizes))
    for form in forms:
        cells = []
        for size in sizes:
            row = next((r for r in results if r["form"] == form and str(r["size"]) == str(size)), None)
            cells.append(f"{row['correct']}/{row['judged']}" if row and row.get("judged") else "-")
        print(f"{form:8s} " + "  ".join(f"{c:>10s}" for c in cells))
    print(f"\nwritten: {json_path}")


if __name__ == "__main__":
    main()
