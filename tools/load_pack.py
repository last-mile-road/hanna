"""Load the Mozambique pack into the run ledger.

Idempotent: concepts, form structure, cards and ground truth are upserted. This is
dev tooling, not the country onboarding UI.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import settings, store
from core.db import connect
from core.forms import load_form_pack

PROFILES = [
    {
        "id": "or-routing-v1",
        "provider": "mixed",
        "model": "routing",
        "settings": {},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Per-block routing. The actual model is recorded on each block_run.",
    },
    {
        "id": "or-qwen3vl-8b-v1",
        "provider": "openrouter",
        "model": "qwen/qwen3-vl-8b-instruct",
        "settings": {"temperature": 0, "max_tokens": 1500},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "First slice profile. qwen3-vl-8b-instruct did 146 of 153 v5 jobs.",
    },
    {
        "id": "or-qwen35-9b-low",
        "provider": "openrouter",
        "model": "qwen/qwen3.5-9b",
        "settings": {"temperature": 0, "max_tokens": 2000, "reasoning_effort": "low", "parallel": 1},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Qwen 3.5 9B with capped reasoning; closest available to the 8B class.",
    },
    {
        "id": "or-qwen35-9b-v1",
        "provider": "openrouter",
        "model": "qwen/qwen3.5-9b",
        "settings": {"temperature": 0, "max_tokens": 1500},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Qwen 3.5 family, 9B. Native multimodal.",
    },
    {
        "id": "or-qwen35-27b-v1",
        "provider": "openrouter",
        "model": "qwen/qwen3.5-27b",
        "settings": {"temperature": 0, "max_tokens": 1500},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Qwen 3.5 family, 27B. Native multimodal.",
    },
    {
        "id": "box-qwen35-9b-v1",
        "provider": "modal",
        "model": "hanna-9b",
        "settings": {"temperature": 0, "max_tokens": 1500},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Self-hosted Qwen3.5-9B on the Modal box. Thinking disabled at the "
                 "proxy; point OPENROUTER_BASE_URL at <box>/llm/v1 and the key at the "
                 "box secret.",
    },
    {
        "id": "box-qwen35-9b-v1-tuned",
        "provider": "modal",
        "model": "hanna-9b",
        "settings": {"temperature": 0.7, "top_p": 0.8, "max_tokens": 1500},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "The model card's instruct-mode sampling (temp 0.7 / top_p 0.8), "
                 "thinking disabled — the accuracy comparison against the greedy run.",
    },
    {
        "id": "or-qwen38-27b-v1",
        "provider": "openrouter",
        "model": "qwen/qwen3.8-27b",
        "settings": {"temperature": 0, "max_tokens": 1500},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Qwen 3.8 family, 27B. Native multimodal.",
    },
    {
        "id": "or-qwen38-27b-v2",
        "provider": "openrouter",
        "model": "qwen/qwen3.8-27b",
        "settings": {"temperature": 0, "max_tokens": 4000},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Qwen 3.8 27B with a larger output budget; the first run spent its tokens before returning JSON.",
    },
    {
        "id": "or-qwen35-27b-low",
        "provider": "openrouter",
        "model": "qwen/qwen3.5-27b",
        "settings": {"temperature": 0, "max_tokens": 2000, "reasoning_effort": "low", "parallel": 1},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Qwen 3.5 27B with a capped reasoning effort; sequential.",
    },
    {
        "id": "or-qwen35-27b-v1-p1",
        "provider": "openrouter",
        "model": "qwen/qwen3.5-27b",
        "settings": {"temperature": 0, "max_tokens": 1500, "parallel": 1},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Qwen 3.5 27B, sequential calls; the concurrency test showed provider queueing.",
    },
    {
        "id": "or-qwen35-27b-v1-tuned",
        "provider": "openrouter",
        "model": "qwen/qwen3.5-27b",
        "settings": {"temperature": 0.3, "top_p": 0.95, "max_tokens": 2000, "parallel": 1},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Qwen 3.5 27B with a small temperature and nucleus sampling; sequential calls.",
    },
    {
        "id": "or-qwen38-27b-low",
        "provider": "openrouter",
        "model": "qwen/qwen3.8-27b",
        "settings": {"temperature": 0.3, "top_p": 0.95, "max_tokens": 8000, "reasoning_effort": "low", "parallel": 1},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Qwen 3.8 27B tuned for a reasoning budget: lower effort, larger output, small temperature.",
    },
    {
        "id": "or-qwen35-35b-a3b-v1",
        "provider": "openrouter",
        "model": "qwen/qwen3.5-35b-a3b",
        "settings": {"temperature": 0, "max_tokens": 1500},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "The model named in the v5 config; never measured there.",
    },
    {
        "id": "or-qwen3vl-8b-v1-up2",
        "provider": "openrouter",
        "model": "qwen/qwen3-vl-8b-instruct",
        "settings": {"temperature": 0, "max_tokens": 1500, "upscale": 2},
        "prompt_version": "v1",
        "crop_strategy": "block",
        "notes": "Visual blocks only: 2x upscale before the model sees the crop.",
    },
    {
        "id": "or-qwen3vl-8b-v1-tpl",
        "provider": "openrouter",
        "model": "qwen/qwen3-vl-8b-instruct",
        "settings": {"temperature": 0, "max_tokens": 1500},
        "prompt_version": "v1",
        "crop_strategy": "block_plus_template",
        "notes": "Visual blocks only: filled crop plus the blank template at the same position.",
    },
]


def ground_truth_rows(sheet_path: Path, pack) -> tuple[list[dict], list[str], list[str]]:
    data = json.loads(sheet_path.read_text())
    by_concept: dict[str, list] = {}
    for pair in pack.pairs:
        by_concept.setdefault(pair.concept, []).append(pair)

    rows: list[dict] = []
    unmapped: list[str] = []
    ambiguous: list[str] = []

    def add(concept_id: str, value, source: str) -> None:
        candidates = by_concept.get(concept_id, [])
        if len(candidates) == 1:
            pair = candidates[0]
            # Last writer wins: the sidecar is applied after the sheet, so an identifying
            # value overrides the sheet rather than colliding with it on (card, gt_key).
            rows[:] = [row for row in rows if row["pair_id"] != pair.id]
            rows.append(
                {
                    "gt_key": pair.id,
                    "concept_id": concept_id,
                    "pair_id": pair.id,
                    "value": str(value),
                    "status": "verified",
                    "scoreable": True,
                    "source": source,
                }
            )
        elif not candidates:
            unmapped.append(concept_id)
        else:
            ambiguous.append(concept_id)

    for concept_id, field in data["fields"].items():
        if field.get("status") == "verified" and field.get("value") is not None:
            add(concept_id, field["value"], field.get("source") or "sheet")

    sidecar = sheet_path.parent / "_identifying" / sheet_path.name
    if sidecar.exists():
        for concept_id, value in json.loads(sidecar.read_text()).items():
            add(concept_id, value, "identifying sidecar")

    return rows, unmapped, ambiguous


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="mozambique")
    ap.add_argument("--form-file", default="form.draft.yaml")
    ap.add_argument("--version", type=int, default=1)
    ap.add_argument("--images-dir", type=Path, default=settings.IMAGES_DIR)
    args = ap.parse_args()

    pack = load_form_pack(args.country, args.form_file)
    print(
        f"pack: {pack.form['id']} v{args.version} | "
        f"{len(pack.blocks)} blocks | {len(pack.pairs)} pairs | "
        f"{len(pack.concepts)} concepts"
    )

    with connect() as conn:
        country_id = store.upsert_country(conn, args.country, args.country.title())
        store.upsert_concepts(conn, pack.concepts)
        form_id = store.upsert_form(conn, country_id, pack, version=args.version)
        for profile in PROFILES:
            store.upsert_profile(conn, profile)

        gt_dir = settings.COUNTRIES_DIR / args.country / "ground_truth"
        total_rows = 0
        for sheet_path in sorted(gt_dir.glob("form*.json")):
            form_name = sheet_path.stem
            image_path = args.images_dir / f"{form_name}.jpg"
            if not image_path.exists():
                print(f"  {form_name}: image missing, skipped")
                continue
            card_id = store.upsert_card(conn, country_id, image_path)
            rows, unmapped, ambiguous = ground_truth_rows(sheet_path, pack)
            store.replace_ground_truth(conn, card_id, rows, version=args.version)
            total_rows += len(rows)
            note = []
            if unmapped:
                note.append(f"unmapped {sorted(set(unmapped))}")
            if ambiguous:
                note.append(f"ambiguous {sorted(set(ambiguous))}")
            print(f"  {form_name}: {len(rows)} scoreable values; " + "; ".join(note))

        conn.commit()
        print(f"loaded: form_id={form_id}, ground-truth rows={total_rows}")


if __name__ == "__main__":
    main()
