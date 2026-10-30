"""Compare the two local OCR readers (MinerU, PaddleOCR-VL) against the routed pipeline.

Phase 0 addendum, 2026-10-30. The owner installed MinerU 4.0.12 (flash tier, local) and
PaddleOCR-VL 1.6 0.9B (Ollama). Both return *text*, not key/value pairs, so this script
measures the ceiling: is the true value present in the reader's output at all?

Three granularities, because they answer different questions:

  block  - the same crop the VLM receives; tests reading order and label/value adjacency
  field  - one crop per declared pair box (demographics only); isolates the reader from the
           block-level mapping problem
  card   - the whole aligned card

Choice, tick and grid cells are excluded from the automatic score: the printed option is
always in the text, the mark is not. Those blocks are scored by hand in the report.

    python benchmarks/readers_compare.py --forms form16,form19 --mode block
"""

from __future__ import annotations

import argparse
import base64
import json
import re
import subprocess
import sys
import time
import unicodedata
import urllib.request
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

from core import settings  # noqa: E402
from core.align import align_card, crop_block, save_jpeg  # noqa: E402
from core.db import connect  # noqa: E402
from core.forms import load_concepts, load_form_pack  # noqa: E402
from benchmark_harness import GroundTruth  # noqa: E402

STRICT_TYPES = {"phone_number", "code_number", "date", "date_or_age", "integer", "percent"}
TEXT_TYPES = {"person_name", "text"}
SKIP_TYPES = {"choice_single", "tick_box", "json"}

WORK = Path("/home/sam/tmp/opencode/readers")
PADDLE_URL = "http://127.0.0.1:8118/v1/chat/completions"
PADDLE_MODEL = "paddleocr-vl"

# PaddleOCR-VL has six trained element prompts. The prompt decides the output shape:
# "Table Recognition:" keeps cell order, "Spotting:" keeps reading order and catches
# handwritten marks the table path drops. Ollama's chat template cannot do this; the
# official GGUF served by llama.cpp can.
PADDLE_PROMPTS = {
    "general_demographic": "OCR:",
    "clinical_history": "OCR:",
    "classification": "OCR:",
    "def_exam": "Table Recognition:",
    "nerve_assessment": "Spotting:",
    "hand_feet_assessment": "OCR:",
    "skin_patch": "OCR:",
    "treatment_follow": "Table Recognition:",
}


# ---------------------------------------------------------------- normalisation

def norm(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded.lower()).strip()


def digits(value: str) -> str:
    return re.sub(r"\D", "", str(value))


def tokens(value: str) -> list[str]:
    stop = {"the", "and", "with", "from", "that", "this"}
    return [t for t in re.split(r"[^a-z0-9]+", norm(value)) if len(t) >= 4 and t not in stop]


def digit_hit(value: str, text_digits: str) -> bool:
    """The full digit run must appear. One concession: a country code on either side.

    A prefix match is not enough — 82543210 and 825432620 share six digits and are
    different phone numbers.
    """
    raw = digits(value).lstrip("0")
    if not raw:
        return False
    variants = {raw}
    if raw.startswith("258"):
        variants.add(raw[3:])
    else:
        variants.add("258" + raw)
    return any(variant and variant in text_digits for variant in variants)


def date_forms(value: str) -> list[str]:
    from datetime import date

    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return [norm(value)]
    return [
        parsed.isoformat(),
        parsed.strftime("%d/%m/%Y"),
        parsed.strftime("%Y/%m/%d"),
        parsed.strftime("%d-%m-%Y"),
        parsed.strftime("%d.%m.%Y"),
        parsed.strftime("%Y%m%d"),
    ]


def value_present(value: str, value_type: str, text_raw: str) -> bool | None:
    """Is the true value findable in the reader text? None = not scoreable this way."""
    text_norm, text_digits = norm(text_raw), digits(text_raw)
    if value_type == "code_number":
        alnum = re.sub(r"[^a-z0-9]", "", norm(value))
        if len(alnum) < 5:
            return None
        return alnum in re.sub(r"[^a-z0-9]", "", text_norm)
    if value_type == "phone_number":
        return digit_hit(value, text_digits)
    if value_type in {"date", "date_or_age"}:
        if digits(value).isdigit() and len(digits(value)) == 4:
            return digits(value) in text_digits
        return any(form in text_norm for form in date_forms(str(value)))
    if value_type in {"integer", "percent"}:
        raw = digits(value)
        return len(raw) >= 2 and raw in text_digits
    if value_type == "person_name":
        wanted = tokens(value)
        if not wanted:
            return None
        return all(token in text_norm for token in wanted)
    if value_type == "text":
        wanted = tokens(value)
        if not wanted:
            return None
        found = sum(1 for token in wanted if token in text_norm)
        # Short values are names: every token must survive. Long free text is a
        # description and is judged on the distinctive-token share.
        threshold = 1.0 if len(wanted) <= 3 else 0.6
        return found / len(wanted) >= threshold
    return None


# ---------------------------------------------------------------- readers

def run_mineru(image: Path, dest: Path) -> tuple[str, float]:
    started = time.monotonic()
    proc = subprocess.run(
        ["mineru", "parse", str(image), "-o", str(dest), "-f", "markdown",
         "--tier", "flash", "--force"],
        capture_output=True, text=True, timeout=300,
    )
    elapsed = time.monotonic() - started
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip()[-400:] or "mineru failed")
    return dest.read_text(), elapsed


def run_paddle(image: Path, dest: Path, prompt: str = "OCR:") -> tuple[str, float]:
    payload = {
        "model": PADDLE_MODEL,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {
                    "url": "data:image/jpeg;base64,"
                           + base64.b64encode(image.read_bytes()).decode()}},
            ],
        }],
        "temperature": 0,
        "max_tokens": 1200,
        "stream": False,
    }
    req = urllib.request.Request(
        PADDLE_URL,
        data=json.dumps(payload).encode(),
        headers={"Content-Type": "application/json"},
    )
    started = time.monotonic()
    with urllib.request.urlopen(req, timeout=300) as response:
        body = json.load(response)
    elapsed = time.monotonic() - started
    text_out = body["choices"][0]["message"]["content"]
    dest.write_text(text_out)
    return text_out, elapsed


# ---------------------------------------------------------------- pipeline baseline

def pipeline_run(form: str) -> str | None:
    with connect() as conn:
        return conn.execute(
            text(
                """
                SELECT r.id FROM runs r JOIN cards c ON c.id = r.card_id
                 WHERE c.image_path LIKE :like AND r.status = 'done'
                   AND r.purpose LIKE 'route-compare%'
                 ORDER BY r.started_at DESC LIMIT 1
                """
            ),
            {"like": f"%/{form}.jpg"},
        ).scalar()


def pipeline_correct(run_id: str | None) -> dict[str, bool]:
    if run_id is None:
        return {}
    with connect() as conn:
        rows = conn.execute(
            text(
                "SELECT concept_id, correct FROM observations "
                "WHERE run_id = :run AND correct IS NOT NULL"
            ),
            {"run": run_id},
        ).mappings()
        return {row["concept_id"]: bool(row["correct"]) for row in rows}


# ---------------------------------------------------------------- crop helpers

def padded(bbox: list[int], pad_x: int, pad_y: int, width: int, height: int) -> list[int]:
    x1, y1, x2, y2 = bbox
    return [max(0, x1 - pad_x), max(0, y1 - pad_y),
            min(width, x2 + pad_x), min(height, y2 + pad_y)]


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default="form16,form19,form21,form22,form23,form25,form30")
    ap.add_argument("--blocks", default="general_demographic,clinical_history,"
                                       "def_exam,hand_feet_assessment,nerve_assessment,"
                                       "treatment_follow")
    ap.add_argument("--mode", default="block", choices=["block", "field", "card"])
    ap.add_argument("--readers", default="mineru,paddle")
    ap.add_argument("--rescore", action="store_true",
                    help="reuse saved reader text instead of calling the models")
    ap.add_argument("--out", default=str(ROOT / "output" / "readers_compare.json"))
    args = ap.parse_args()

    readers = args.readers.split(",")
    concepts = load_concepts()
    pack = load_form_pack("mozambique")
    template = settings.COUNTRIES_DIR / "mozambique" / "template.jpg"
    report: list[dict] = []

    for form in args.forms.split(","):
        image = settings.IMAGES_DIR / f"{form}.jpg"
        if not image.exists():
            print(f"{form}: image missing, skipped")
            continue
        truth = GroundTruth.from_json(
            settings.COUNTRIES_DIR / "mozambique" / "ground_truth" / f"{form}.json",
            settings.IMAGES_DIR,
        ).expected
        baseline = pipeline_correct(pipeline_run(form))

        aligned = align_card(image, template)
        height, width = aligned.image.shape[:2]
        form_dir = WORK / form
        form_dir.mkdir(parents=True, exist_ok=True)

        if args.mode == "card":
            crops = [("whole_card", padded([0, 0, width, height], 0, 0, width, height))]
        elif args.mode == "block":
            wanted = args.blocks.split(",")
            crops = [
                (block.id, padded(block.bbox, 6, 6, width, height))
                for block in pack.blocks if block.id in wanted and block.bbox
            ]
        else:  # field
            wanted = args.blocks.split(",")
            crops = []
            for block in pack.blocks:
                if block.id not in wanted:
                    continue
                for pair in block.pairs:
                    if pair.bbox:
                        crops.append(
                            (f"{block.id}__{pair.id}",
                             padded(pair.bbox, 18, 12, width, height))
                        )
        pairs_by_key = {f"{b.id}__{p.id}": p for b in pack.blocks for p in b.pairs}
        pairs_by_key.update({b.id: None for b in pack.blocks})

        for key, bbox in crops:
            crop = crop_block(aligned.image, bbox)
            jpg = form_dir / f"{key}.jpg"
            save_jpeg(crop, jpg)

            if key == "whole_card":
                scored_pairs = [p for b in pack.blocks for p in b.pairs]
            elif "_block_" in key or key in pairs_by_key:
                block_id = key.split("__")[0]
                block = next((b for b in pack.blocks if b.id == block_id), None)
                scored_pairs = block.pairs if block else []
            else:
                scored_pairs = []
            single = pairs_by_key.get(key)
            if single is not None:
                scored_pairs = [single]

            for reader in readers:
                ext = "md" if reader == "mineru" else "txt"
                dest = form_dir / f"{key}.{reader}.{ext}"
                block_id = key.split("__")[0]
                prompt = PADDLE_PROMPTS.get(block_id, "OCR:")
                try:
                    if args.rescore:
                        if not dest.exists():
                            continue
                        output, seconds = dest.read_text(), 0.0
                    elif reader == "mineru":
                        output, seconds = run_mineru(jpg, dest)
                    else:
                        output, seconds = run_paddle(jpg, dest, prompt)
                except Exception as exc:  # noqa: BLE001 - record and continue
                    print(f"  {form} {key} {reader}: ERROR {exc}")
                    report.append({"form": form, "crop": key, "reader": reader,
                                   "error": str(exc)})
                    continue

                rows, hits, judged = [], 0, 0
                for pair in scored_pairs:
                    truth_value = truth.get(pair.concept)
                    value_type = pair.value_type or concepts.get(pair.concept, {}).get(
                        "value_type", "text")
                    if truth_value is None or value_type in SKIP_TYPES:
                        continue
                    present = value_present(truth_value, value_type, output)
                    if present is None:
                        continue
                    judged += 1
                    hits += int(present)
                    rows.append({
                        "pair": pair.id,
                        "concept": pair.concept,
                        "truth": truth_value,
                        "present": present,
                        "pipeline": baseline.get(pair.concept),
                    })
                report.append({
                    "form": form, "crop": key, "reader": reader, "seconds": round(seconds, 2),
                    "chars": len(output), "judged": judged, "hits": hits,
                    "rows": rows,
                })
                print(f"  {form} {key:42s} {reader:7s} {hits}/{judged} {seconds:5.1f}s")
                dest.with_suffix(dest.suffix + ".json").write_text(
                    json.dumps(rows, indent=1, ensure_ascii=False))

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
