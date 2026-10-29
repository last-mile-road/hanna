"""Compare MinerU's text with the pipeline, field type by field type.

Phase 0 addendum, 2026-10-29. MinerU returns text and HTML tables, not key/value pairs, so
three different questions have to be asked separately — mixing them into one "accuracy"
number is misleading:

  1. **Identifiers, numbers and dates** — can the correct value be found in MinerU's text?
     Compare against the pipeline on the same fields. This is the strictest and most
     decision-relevant group.
  2. **Names, places and free text** — is the value present, allowing spelling and accent
     differences? Measured as the share of distinctive tokens found.
  3. **Choice and tick fields** — NOT scoreable from text alone: the printed option is
     always in the text, the tick is not. Excluded, and reported separately; the nerve and
     disability tables were hand-scored instead (see `docs/mineru-assessment.md`).

    python benchmarks/mineru_compare.py
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import unicodedata
from pathlib import Path

from sqlalchemy import text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "benchmarks"))

from core import settings  # noqa: E402
from core.db import connect  # noqa: E402
from core.forms import load_concepts  # noqa: E402
from benchmark_harness import GroundTruth  # noqa: E402

STRICT_TYPES = {"phone_number", "code_number", "date", "date_or_age"}
TEXT_TYPES = {"person_name", "text"}
NUMERIC_TYPES = {"integer", "percent"}


def norm(value: str) -> str:
    folded = unicodedata.normalize("NFKD", str(value))
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    return re.sub(r"\s+", " ", folded.lower()).strip()


def digits(value: str) -> str:
    return re.sub(r"\D", "", str(value))


def tokens(value: str) -> list[str]:
    return [t for t in re.split(r"[^a-z0-9]+", norm(value)) if len(t) >= 4]


def digit_hit(value: str, text_digits: str) -> bool:
    """A phone may carry a country code on one side only; a 6+ digit run is enough."""
    raw = digits(value)
    if len(raw) < 6:
        return False
    for size in range(len(raw), 5, -1):
        for start in range(0, len(raw) - size + 1):
            if raw[start:start + size] in text_digits:
                return True
    return False


def text_hit(value: str, text_norm: str) -> bool:
    wanted = tokens(value)
    if not wanted:
        return False
    found = sum(1 for token in wanted if token in text_norm)
    return found / len(wanted) >= 0.6


def date_forms(value: str) -> list[str]:
    from datetime import date

    try:
        parsed = date.fromisoformat(str(value))
    except ValueError:
        return [str(value)]
    return [
        parsed.isoformat(),
        parsed.strftime("%d/%m/%Y"),
        parsed.strftime("%Y/%m/%d"),
        parsed.strftime("%d-%m-%Y"),
    ]


def load_pipeline(form: str) -> dict[str, bool]:
    with connect() as conn:
        run = conn.execute(
            text(
                """
                SELECT r.id FROM runs r
                  JOIN cards c ON c.id = r.card_id
                 WHERE c.image_path LIKE :like
                 ORDER BY r.started_at DESC
                 LIMIT 1
                """
            ),
            {"like": f"%/{form}.jpg"},
        ).scalar()
        if run is None:
            return {}
        rows = conn.execute(
            text(
                "SELECT concept_id, correct FROM observations "
                "WHERE run_id = :run AND correct IS NOT NULL"
            ),
            {"run": run},
        ).mappings()
        return {row["concept_id"]: bool(row["correct"]) for row in rows}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default="form16=debug_aligned,form19=aligned,form21=form21")
    ap.add_argument("--dir", default="/home/sam/tmp/opencode/mineru")
    ap.add_argument("--out", default=str(ROOT / "output" / "mineru_compare.json"))
    args = ap.parse_args()

    concepts = load_concepts()
    base = Path(args.dir)
    report = []

    for item in args.map.split(","):
        form, stem = (part.strip() for part in item.split("="))
        md = next(base.glob(f"{stem}*/{stem}.md"), None)
        if md is None:
            print(f"missing markdown for {stem}")
            continue
        raw = md.read_text()
        text_norm, text_digits = norm(raw), digits(raw)

        truth = GroundTruth.from_json(
            settings.COUNTRIES_DIR / "mozambique" / "ground_truth" / f"{form}.json",
            settings.IMAGES_DIR,
        ).expected
        pipeline = load_pipeline(form)

        groups = {"strict": [], "text": [], "excluded": []}
        for concept_id, value in truth.items():
            concept = concepts.get(concept_id, {})
            value_type = concept.get("value_type", "text")

            if value_type in STRICT_TYPES:
                if value_type in {"date", "date_or_age"} and not isinstance(value, str):
                    hit = str(value) in text_norm
                elif value_type in {"phone_number", "code_number"}:
                    hit = digit_hit(value, text_digits) or norm(value) in text_norm
                else:
                    hit = any(form_v in text_norm for form_v in date_forms(str(value)))
                groups["strict"].append(
                    {"concept": concept_id, "truth": str(value), "hit": hit,
                     "pipeline": pipeline.get(concept_id)}
                )
            elif value_type in TEXT_TYPES:
                groups["text"].append(
                    {"concept": concept_id, "truth": str(value),
                     "hit": text_hit(str(value), text_norm),
                     "pipeline": pipeline.get(concept_id)}
                )
            elif value_type in NUMERIC_TYPES and len(digits(value)) >= 3:
                groups["strict"].append(
                    {"concept": concept_id, "truth": str(value),
                     "hit": digits(value) in text_digits,
                     "pipeline": pipeline.get(concept_id)}
                )
            else:
                groups["excluded"].append(concept_id)

        def summarise(rows: list[dict]) -> dict:
            judged = [r for r in rows if r["pipeline"] is not None]
            return {
                "n": len(rows),
                "mineru_hits": sum(r["hit"] for r in rows),
                "pipeline_judged": len(judged),
                "pipeline_correct": sum(bool(r["pipeline"]) for r in judged),
                "mineru_only": [r["concept"] for r in rows
                                if r["hit"] and r["pipeline"] is False],
                "pipeline_only": [r["concept"] for r in rows
                                  if not r["hit"] and r["pipeline"] is True],
            }

        entry = {
            "form": form,
            "markdown": str(md),
            "strict": {**summarise(groups["strict"]), "rows": groups["strict"]},
            "text": {**summarise(groups["text"]), "rows": groups["text"]},
            "choice_fields_excluded": len(groups["excluded"]),
        }
        report.append(entry)

        print(f"{form}:")
        for label in ("strict", "text"):
            s = entry[label]
            print(
                f"  {label:6s} MinerU present {s['mineru_hits']}/{s['n']} | "
                f"pipeline correct {s['pipeline_correct']}/{s['pipeline_judged']}"
            )
            if s["mineru_only"]:
                print(f"         MinerU has it, pipeline missed: {s['mineru_only']}")
            if s["pipeline_only"]:
                print(f"         pipeline right, MinerU text missing: {s['pipeline_only']}")
        print(f"  choice/tick fields excluded: {entry['choice_fields_excluded']}")

    Path(args.out).parent.mkdir(parents=True, exist_ok=True)
    Path(args.out).write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nwritten: {args.out}")


if __name__ == "__main__":
    main()
