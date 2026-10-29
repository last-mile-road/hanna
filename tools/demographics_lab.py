"""Demographics block lab: compare prompt strategies on the same three crops.

Not part of the pipeline. It loads the latest demographics crop for form16,
form19 and form21, runs several prompt shapes, maps the output to the block's
pair ids, and scores against the ledger's ground truth.

Usage:
  python tools/demographics_lab.py --strategies fields,labels,transcribe,rows --models 8b,9b
"""

from __future__ import annotations

import argparse
import dataclasses
import json
import re
import sys
import unicodedata
from pathlib import Path

import cv2
from sqlalchemy import select, text

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import align, normalise, settings, store
from core.db import connect
from core.forms import Block, Pair, load_form_pack
from core.inference import OpenRouter
from core.parse import parse_json_response
from core.prompts import build_prompt
from core.schema import block_runs, cards, forms, profiles, runs

LABELS = {
    "unique_nr": "Número Individual do doente de Lepra (NIL)",
    "patient_name": "Nome completo",
    "birth_year": "Ano de nascimento / Idade",
    "cellphone_nr": "Celular",
    "district": "Distrito",
    "locality": "Localidade",
    "patient_reference": "Pessoa de Referência",
    "village_name": "Unidade Sanitária / Aldeia",
    "gender": "Sexo (M / F)",
}

SPATIAL = """
Spatial layout of this block:
- The NIL box is at the top, above the name.
- Nome completo is the wide line under the NIL box.
- Ano de nascimento/Idade and Celular are on the same row: birth year on the left,
  phone on the right. Sexo M/F tickboxes are at the far right of that row.
- Distrito and Localidade are on the same row: district left, locality right.
- Pessoa de Referência is the next line.
- Unidade Sanitária/Aldeia is the bottom line.
Do not merge values from adjacent rows.
"""

TRANSCRIBE = """Transcribe all handwritten text in this crop of a Mozambique leprosy
clinic card. The printed labels are in Portuguese. Return one JSON object:

{{"lines": [{{"label": "<printed label>", "value": "<handwritten value>"}}]}}

Read top to bottom. Keep the printed label and the handwritten value together.
Do not translate. Do not return a printed label as a value. Omit blank lines.
Return JSON only, no markdown. The handwriting is Portuguese.
"""

ROWS = """Read this crop of a Mozambique leprosy clinic card row by row.
For each printed label, return the handwritten value on the same row.
Return one JSON object:

{{"rows": [{{"row": 1, "label": "<printed label>", "value": "<handwritten value>"}}]}}

Number rows from the top. Do not merge adjacent rows. Do not return a printed
label as a value. Omit blank rows. Return JSON only, no markdown.
The handwriting is Portuguese.
"""


def key(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", text.lower()).strip()


def label_to_pair(label: str) -> str | None:
    k = key(label)
    if "nil" in k or "numero individual" in k:
        return "unique_nr"
    if "nome" in k:
        return "patient_name"
    if "localidade" in k:
        return "locality"
    if "distrito" in k:
        return "district"
    if "nascimento" in k or re.search(r"\bidade\b", k):
        return "birth_year"
    if "celular" in k or "contacto" in k or "telefone" in k:
        return "cellphone_nr"
    if "referencia" in k:
        return "patient_reference"
    if "unidade" in k or "aldeia" in k or "aldela" in k:
        return "village_name"
    if "sexo" in k or "genero" in k:
        return "gender"
    return None


def map_output(parsed: dict, strategy: str, pair_ids: list[str]) -> dict[str, str]:
    if strategy in {"fields", "labels", "spatial"}:
        return {k: v for k, v in parsed.items() if k in pair_ids}

    lines = parsed.get("lines") or parsed.get("rows") or []
    out: dict[str, str] = {}
    if isinstance(lines, list):
        for item in lines:
            if not isinstance(item, dict):
                continue
            label = item.get("label") or item.get("printed_label") or ""
            value = item.get("value") or item.get("text") or ""
            if value in (None, ""):
                continue
            pair = label_to_pair(str(label))
            if pair:
                out[pair] = str(value)
    return out


def build_prompts(block: Block, pairs: list[Pair], concepts: dict, strategy: str) -> str:
    if strategy == "fields":
        return build_prompt(block, pairs, concepts)
    if strategy == "spatial":
        return build_prompt(block, pairs, concepts) + "\n" + SPATIAL
    if strategy == "labels":
        labelled = [
            dataclasses.replace(p, label_local=LABELS.get(p.id, p.label_local))
            for p in pairs
        ]
        return build_prompt(block, labelled, concepts) + "\n" + SPATIAL
    if strategy == "transcribe":
        return TRANSCRIBE
    if strategy == "rows":
        return ROWS
    raise ValueError(strategy)


def load_case(card_name: str):
    with connect() as conn:
        card = conn.execute(
            select(cards).where(cards.c.image_path.like(f"%/{card_name}.jpg"))
        ).mappings().one()
        form = conn.execute(select(forms).order_by(forms.c.version.desc()).limit(1)).mappings().one()
        blocks, pairs, concepts = store.form_rows(conn, form["id"])
        truth = store.ground_truth_map(conn, card["id"], version=1)
        latest = conn.execute(
            select(block_runs.c.crop_path, block_runs.c.run_id)
            .join(runs, runs.c.id == block_runs.c.run_id)
            .where(block_runs.c.block_id == "general_demographic")
            .where(runs.c.card_id == card["id"])
            .order_by(block_runs.c.id.desc())
            .limit(1)
        ).mappings().first()
    block = next(b for b in blocks if b.id == "general_demographic")
    block_pairs = [p for p in pairs if p.block_id == "general_demographic"]
    ocr_path = Path(latest["crop_path"])
    raw_path = Path(str(ocr_path).replace("_ocr.jpg", "_raw.jpg"))
    aligned_path = ocr_path.parent / "aligned.jpg"
    return block, block_pairs, concepts, truth, ocr_path, raw_path, aligned_path


LOCATION_CONCEPTS = {"district", "locality", "village"}


def score(case, values: dict[str, str], fuzzy: bool = False):
    block, pairs, concepts, truth, *_ = case
    judged = correct = fuzzy_correct = 0
    misses: list[str] = []
    fuzzy_misses: list[str] = []
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
            fuzzy_correct += 1
            continue
        misses.append(f"{pair.id}: {values.get(pair.id)!r} != {expected!r}")
        if fuzzy and pair.concept in LOCATION_CONCEPTS:
            from rapidfuzz import fuzz
            ratio = fuzz.ratio(normalise.key(values.get(pair.id) or ""), normalise.key(expected)) / 100
            if ratio >= 0.85:
                fuzzy_correct += 1
                fuzzy_misses.append(f"{pair.id}: {values.get(pair.id)!r} ~ {expected!r} ({ratio:.2f})")
                continue
        fuzzy_misses.append(f"{pair.id}: no match")
    return correct, judged, misses, fuzzy_correct, fuzzy_misses


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--strategies", default="fields,labels,transcribe,rows")
    ap.add_argument("--models", default="8b,9b")
    ap.add_argument("--cards", default="form16,form19,form21")
    ap.add_argument("--image", choices=["ocr", "raw"], default="ocr")
    ap.add_argument("--only-card", default=None)
    ap.add_argument("--fuzzy", action="store_true")
    args = ap.parse_args()

    strategies = args.strategies.split(",")
    model_ids = {
        "8b": "or-qwen3vl-8b-v1",
        "9b": "or-qwen35-9b-v1",
        "27b": "or-qwen35-27b-low",
    }
    with connect() as conn:
        profile_rows = {
            row["id"]: row for row in conn.execute(select(profiles)).mappings()
        }

    cards_list = args.cards.split(",")
    if args.only_card:
        cards_list = [args.only_card]

    out_dir = settings.OUTPUT_DIR / "demographics_lab"
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    client = OpenRouter()

    for card_name in cards_list:
        case = load_case(card_name)
        block, pairs, concepts, truth, ocr_path, raw_path, aligned_path = case
        image_path = ocr_path if args.image == "ocr" else raw_path
        print(f"\n== {card_name} | image={image_path.name}")
        for model_key in args.models.split(","):
            profile = profile_rows[model_ids[model_key]]
            for strategy in strategies:
                pair_ids = [p.id for p in pairs]
                prompt = build_prompts(block, pairs, concepts, strategy) if strategy != "two_stage" else None
                try:
                    if strategy == "two_stage":
                        stage1 = client.complete(
                            TRANSCRIBE, [image_path], profile["model"], profile["settings"]
                        )
                        parsed1 = parse_json_response(stage1.text)
                        lines = parsed1.get("lines") or parsed1.get("rows") or []
                        transcript = []
                        for i, item in enumerate(lines, 1):
                            if isinstance(item, dict):
                                transcript.append(
                                    f"{i}. {item.get('label', '')}: {item.get('value', '')}"
                                )
                            else:
                                transcript.append(f"{i}. {item}")
                        field_lines = "\n".join(
                            f'- "{p.id}": {LABELS.get(p.id, p.concept)}' for p in pairs
                        )
                        prompt2 = (
                            "You are mapping transcribed lines from the demographic block "
                            "of a Mozambique leprosy card to fields.\n"
                            + SPATIAL
                            + "\nTranscribed lines:\n"
                            + "\n".join(transcript)
                            + "\n\nFields to fill:\n"
                            + field_lines
                            + "\n\nReturn one JSON object keyed by the field ids. "
                            "Use null for a blank field. Do not invent values."
                        )
                        stage2 = client.complete(
                            prompt2, [], profile["model"], profile["settings"]
                        )
                        parsed = parse_json_response(stage2.text, pair_ids)
                        values = {k: v for k, v in parsed.items() if k in pair_ids}
                        latency_ms = stage1.latency_ms + stage2.latency_ms
                        cost = (stage1.cost_usd or 0) + (stage2.cost_usd or 0)
                        raw = stage1.text + "\n---STAGE2---\n" + stage2.text
                    else:
                        completion = client.complete(
                            prompt, [image_path], profile["model"], profile["settings"]
                        )
                        parsed = parse_json_response(completion.text, pair_ids)
                        values = map_output(parsed, strategy, pair_ids)
                        latency_ms = completion.latency_ms
                        cost = completion.cost_usd
                        raw = completion.text
                    correct, judged, misses, fuzzy_correct, fuzzy_misses = score(
                        case, values, fuzzy=args.fuzzy
                    )
                    results.append(
                        {
                            "card": card_name,
                            "model": model_key,
                            "strategy": strategy,
                            "correct": correct,
                            "fuzzy_correct": fuzzy_correct,
                            "judged": judged,
                            "misses": misses,
                            "fuzzy_misses": fuzzy_misses,
                            "parsed": parsed,
                            "raw": raw,
                            "latency_ms": latency_ms,
                            "cost": cost,
                        }
                    )
                    extra = f" fuzzy={fuzzy_correct}" if args.fuzzy else ""
                    print(
                        f"  {model_key:3s} {strategy:10s} {correct}/{judged}{extra} "
                        f"{latency_ms:5d} ms | " + "; ".join(misses)
                    )
                except Exception as exc:
                    print(f"  {model_key:3s} {strategy:10s} ERROR {exc}")

    path = out_dir / f"results_{args.image}.json"
    path.write_text(json.dumps(results, indent=2, ensure_ascii=False))
    print(f"\nwritten {path}")

    print("\nSUMMARY")
    for strategy in strategies:
        for model_key in args.models.split(","):
            rows = [r for r in results if r["strategy"] == strategy and r["model"] == model_key]
            correct = sum(r["correct"] for r in rows)
            judged = sum(r["judged"] for r in rows)
            fuzzy = sum(r.get("fuzzy_correct", 0) for r in rows)
            print(
                f"  {model_key:3s} {strategy:10s} {correct}/{judged} exact"
                f" | {fuzzy}/{judged} fuzzy | {len(rows)} cards"
            )


if __name__ == "__main__":
    main()
