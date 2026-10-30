"""Standard-tier pass: MinerU 4.0.12 API and PaddleOCR-VL doc_parser, all blocks.

2026-10-30, third pass. The first pass used MinerU's flash text preview and raw model
text; this one uses the same output the web UIs show:
- MinerU: local API, tier standard (layout + slanet tables + MinerU2.5 VLM).
- Paddle: `paddleocr doc_parser` (layout + element prompt per block).

Text blocks are scored twice: strict, and pragmatic (one-character handwriting slips are
forgiven, as a clinician or the gazetteer would). Tables are scored cell by cell by
`table_compare`.

    python benchmarks/readers_standard.py
    python benchmarks/readers_standard.py --rescore
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

from core import settings  # noqa: E402
from core.db import connect  # noqa: E402
from core.forms import load_concepts, load_form_pack  # noqa: E402
from benchmark_harness import GroundTruth  # noqa: E402
from table_compare import (  # noqa: E402
    BLOCKS as TABLE_BLOCKS, FORMS, WORK as TABLE_WORK, run_mineru, run_paddle,
    score_def_exam, score_nerve, table_matrix,
)
from sqlalchemy import text  # noqa: E402

WORK = Path("/home/sam/tmp/opencode/standard")
INPUTS = Path("/home/sam/tmp/opencode/readers")
STRICT_TYPES = {"phone_number", "code_number", "date", "date_or_age"}
SKIP_TYPES = {"choice_single", "tick_box", "json"}
TEXT_BLOCKS = ["general_demographic", "clinical_history", "hand_feet_assessment",
               "treatment_follow", "skin_patch", "classification"]


def norm(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded.lower()).strip()


def digits(value: str) -> str:
    return re.sub(r"\D", "", str(value))


def tokens(value: str) -> list[str]:
    stop = {"the", "and", "with", "from", "that", "this"}
    return [t for t in re.split(r"[^a-z0-9]+", norm(value)) if len(t) >= 4 and t not in stop]


def distance(a: str, b: str) -> int:
    if abs(len(a) - len(b)) > 1:
        return 9
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(min(previous[j] + 1, current[j - 1] + 1,
                               previous[j - 1] + (char_a != char_b)))
        previous = current
    return previous[-1]


def token_hit(token: str, haystack: str) -> bool:
    """Match a token allowing one insertion/deletion/substitution and lost word spaces.

    Cell text is often concatenated ("SledgehammeAno", "Naman humbire"), so a plain word
    split misses one-character handwriting slips that a clinician would read through.
    """
    haystack = re.sub(r"\s+", "", haystack)
    token = re.sub(r"\s+", "", token)
    if token in haystack:
        return True
    if len(token) < 5:
        return False
    for size in (len(token) - 1, len(token) + 1):
        if size <= 0:
            continue
        for start in range(0, max(1, len(haystack) - size + 1)):
            if distance(token, haystack[start:start + size]) <= 1:
                return True
    return False


def digit_hit(value: str, text_digits: str) -> bool:
    raw = digits(value).lstrip("0")
    if not raw:
        return False
    variants = {raw, "258" + raw}
    if raw.startswith("258"):
        variants.add(raw[3:])
    return any(variant in text_digits for variant in variants)


def date_forms(value: str) -> list[str]:
    from datetime import date

    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return [norm(value)]
    return [parsed.isoformat(), parsed.strftime("%d/%m/%Y"), parsed.strftime("%Y/%m/%d"),
            parsed.strftime("%d-%m-%Y"), parsed.strftime("%Y%m%d")]


def present(value: str, value_type: str, text_raw: str, fuzzy: bool) -> bool | None:
    text_norm, text_digits = norm(text_raw), digits(text_raw)
    if value_type == "code_number":
        alnum = re.sub(r"[^a-z0-9]", "", norm(value))
        if len(alnum) < 5:
            return None
        return alnum in re.sub(r"[^a-z0-9]", "", text_norm)
    if value_type == "phone_number":
        return digit_hit(value, text_digits)
    if value_type in {"date", "date_or_age"}:
        if len(digits(value)) == 4:
            return digits(value) in text_digits
        return any(form in text_norm for form in date_forms(str(value)))
    if value_type in {"integer", "percent"}:
        raw = digits(value)
        return len(raw) >= 2 and raw in text_digits
    if value_type in {"person_name", "text"}:
        wanted = tokens(value)
        if not wanted:
            return None
        if fuzzy:
            found = sum(1 for token in wanted if token_hit(token, text_norm))
            return found == len(wanted) if len(wanted) <= 3 else found / len(wanted) >= 0.6
        if value_type == "person_name":
            return all(token in text_norm for token in wanted)
        threshold = 1.0 if len(wanted) <= 3 else 0.6
        return sum(1 for token in wanted if token in text_norm) / len(wanted) >= threshold
    return None


def plain(fragment: str) -> str:
    without_images = re.sub(r"<img[^>]*>", " ", fragment or "", flags=re.I)
    return re.sub(r"<[^>]+>", " ", without_images)


def pipeline_correct(form: str) -> dict[str, bool]:
    with connect() as conn:
        run_id = conn.execute(
            text("""SELECT r.id FROM runs r JOIN cards c ON c.id = r.card_id
                     WHERE c.image_path LIKE :like AND r.status = 'done'
                       AND r.purpose LIKE 'route-compare%'
                     ORDER BY r.started_at DESC LIMIT 1"""),
            {"like": f"%/{form}.jpg"},
        ).scalar()
        if run_id is None:
            return {}
        rows = conn.execute(
            text("SELECT concept_id, correct FROM observations "
                 "WHERE run_id = :run AND correct IS NOT NULL"),
            {"run": run_id},
        ).mappings()
        return {row["concept_id"]: bool(row["correct"]) for row in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readers", default="mineru,paddle")
    ap.add_argument("--rescore", action="store_true")
    ap.add_argument("--forms", default=",".join(FORMS))
    ap.add_argument("--blocks", default=",".join(TABLE_BLOCKS + TEXT_BLOCKS))
    ap.add_argument("--out", default=str(ROOT / "output" / "readers_standard.json"))
    args = ap.parse_args()

    forms = args.forms.split(",")
    blocks = list(dict.fromkeys(args.blocks.split(",")))
    staged = WORK / "inputs"
    staged.mkdir(parents=True, exist_ok=True)
    images = []
    for form in forms:
        for block in blocks:
            source = INPUTS / form / f"{block}.jpg"
            if source.exists():
                target = staged / f"{form}__{block}.jpg"
                if not target.exists():
                    target.write_bytes(source.read_bytes())
                images.append(target)
    print(f"{len(images)} crops")

    outputs: dict[str, dict[str, str]] = {}
    for reader in args.readers.split(","):
        out = WORK / reader
        out.mkdir(parents=True, exist_ok=True)
        cached = {p.stem: p.read_text() for p in out.glob("*.html")}
        if args.rescore and cached:
            outputs[reader] = cached
            print(f"{reader}: {len(cached)} cached")
        else:
            started = __import__("time").monotonic()
            outputs[reader] = run_mineru(images, out) if reader == "mineru" else run_paddle(images, out)
            print(f"{reader}: {len(outputs[reader])} outputs in "
                  f"{__import__('time').monotonic()-started:.0f}s")

    pack = load_form_pack("mozambique")
    concepts = load_concepts()
    report = []
    for form in forms:
        truth = GroundTruth.from_json(
            settings.COUNTRIES_DIR / "mozambique" / "ground_truth" / f"{form}.json",
            settings.IMAGES_DIR,
        ).expected
        baseline = pipeline_correct(form)
        for block in blocks:
            key = f"{form}__{block}"
            entity = next((b for b in pack.blocks if b.id == block), None)
            if entity is None:
                continue
            for reader in outputs:
                fragment = outputs[reader].get(key, "")
                if block in TABLE_BLOCKS:
                    matrix = table_matrix(fragment) if "<table" in fragment else []
                    rows = (score_def_exam(matrix, truth) if block == "def_exam"
                            else score_nerve(matrix, truth))
                    report.append({"form": form, "block": block, "reader": reader,
                                   "kind": "table", "rows": rows,
                                   "table_found": bool(matrix)})
                    continue
                text_raw = plain(fragment)
                rows = []
                for pair in entity.pairs:
                    expected = truth.get(pair.concept)
                    value_type = pair.value_type or concepts.get(pair.concept, {}).get(
                        "value_type", "text")
                    if expected is None or value_type in SKIP_TYPES:
                        continue
                    strict = present(expected, value_type, text_raw, fuzzy=False)
                    loose = present(expected, value_type, text_raw, fuzzy=True)
                    if strict is None and loose is None:
                        continue
                    rows.append({"concept": pair.concept, "pair": pair.id,
                                 "expected": expected, "strict": bool(strict),
                                 "fuzzy": bool(loose), "pipeline": baseline.get(pair.concept)})
                report.append({"form": form, "block": block, "reader": reader,
                               "kind": "text", "rows": rows,
                               "empty": not text_raw.strip()})

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))

    for block in blocks:
        for reader in outputs:
            entries = [e for e in report if e["block"] == block and e["reader"] == reader]
            if not entries:
                continue
            if entries[0]["kind"] == "table":
                ok = sum(r["correct"] for e in entries for r in e["rows"])
                n = sum(len(e["rows"]) for e in entries)
                print(f"{block:24s} {reader:7s} {ok}/{n}")
            else:
                strict = sum(r["strict"] for e in entries for r in e["rows"])
                fuzzy = sum(r["fuzzy"] for e in entries for r in e["rows"])
                n = sum(len(e["rows"]) for e in entries)
                empty = sum(1 for e in entries if e["empty"])
                print(f"{block:24s} {reader:7s} strict {strict}/{n}  pragmatic {fuzzy}/{n}"
                      f"  empty outputs {empty}")
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
