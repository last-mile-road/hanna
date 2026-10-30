"""Tick strips vs the block prompt, measured on the seven truth cards.

Two arms, same 9B model, same cards:

  A. block prompt — the clinical-history block crop and the demographics block crop, as the
     pipeline does today (one call each, nine fields competing for attention);
  B. tick strips — one call per question group, the crop rendered by tools/render_tick_strip
     (boxes only, white margin, big band numbers, question and options in the prompt).

Scored per concept against the sheets. Case category and leprosy classification have no
verified truth in the sheets yet, so they are run and printed but not scored.

    python benchmarks/tick_strip_test.py
"""
from __future__ import annotations

import json, sys
from pathlib import Path

import cv2, yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import align, normalise, settings, store
from core.db import connect
from core.inference import OpenRouter
from core.parse import parse_json_response
from core.prompts import build_prompt
from sqlalchemy import text
from tools.render_tick_strip import render_strip, stack_bands

FORMS = ["form16", "form19", "form21", "form22", "form23", "form25", "form30"]
PROFILE = "or-qwen35-9b-v1"
TMP = Path("/home/sam/tmp/opencode/tick")

GROUPS = [
    {"key": "itching", "concept": "itching", "question": "As lesões da pele dão comichão?",
     "options": ["Yes", "No"], "labels": ["sim", "não"]},
    {"key": "leprosy_contact", "concept": "leprosy_contact",
     "question": "O doente tem familiares/vizinhos/amigos com Lepra?",
     "options": ["Yes", "No"], "labels": ["sim", "não"]},
    {"key": "previous_leprosy_treatment", "concept": "previous_leprosy_treatment",
     "question": "O doente já teve o diagnóstico e tratamento de Lepra no passado?",
     "options": ["Yes", "No"], "labels": ["sim", "não"]},
    {"key": "referred_by", "concept": "referred_by", "question": "Quem trouxe o doente à consulta?",
     "options": ["self referred", "volunteer", "family member", "traditional healer", "other"],
     "labels": ["veio sozinho", "trazido pelo voluntário", "trazido por familiar",
                "transferido pelo médico tradicional", "outra situação"]},
]


def prompt_for(group: dict, n: int) -> str:
    bands = "; ".join(f"band {i + 1} = \"{label}\"" for i, label in enumerate(group["labels"]))
    return (
        f"This image shows {n} numbered tickboxes from a leprosy clinic card, stacked "
        f"vertically in the order they are printed.\n"
        f"Question on the card: \"{group['question']}\"\n{bands}.\n"
        "A band is ticked when there is a handwritten tick, cross or filled mark inside its box. "
        "An empty printed box is not ticked.\n"
        "Return one JSON object and nothing else: {\"ticked\": [<band numbers>]}. "
        "Use an empty list when no band is clearly marked."
    )


def decode_ticked(parsed: dict) -> list[int]:
    value = parsed.get("ticked")
    if isinstance(value, int):
        return [value]
    if isinstance(value, list):
        out = []
        for item in value:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out
    if isinstance(value, str):
        return [int(part) for part in value.replace(",", " ").split() if part.isdigit()]
    return []


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    ui = yaml.safe_load((settings.COUNTRIES_DIR / "mozambique" / "tick_columns.yaml").read_text())
    client = OpenRouter()
    with connect() as conn:
        form = conn.execute(text(
            "select id, template_path from forms order by version desc limit 1")).mappings().one()
        blocks, pairs, concepts = store.form_rows(conn, form["id"])
        profile = {r["id"]: r for r in conn.execute(text("select * from profiles")).mappings()}[PROFILE]
    clinical_block = next(b for b in blocks if b.id == "clinical_history")
    demo_block = next(b for b in blocks if b.id == "general_demographic")

    scores = {"block": {}, "strips": {}}
    rows = []

    for form_name in FORMS:
        truth = json.loads((settings.COUNTRIES_DIR / "mozambique" / "ground_truth" /
                            f"{form_name}.json").read_text())["fields"]
        expired = {k: (v.get("value") if v.get("status") == "verified" else None) for k, v in truth.items()}
        alignment = align.align_card(Path(settings.IMAGES_DIR) / f"{form_name}.jpg",
                                     Path(form["template_path"]))

        # --- arm A: block prompts
        a_values = {}
        for block, keys in ((clinical_block, ["itching", "leprosy_contact",
                                              "previous_leprosy_treatment", "referred_by"]),
                            (demo_block, ["gender"])):
            crop = align.crop_block(alignment.image, block.bbox)
            path = TMP / f"{form_name}_{block.id}.jpg"
            align.save_jpeg(crop, path)
            prompt = build_prompt(block, block.pairs, concepts, country="Mozambique", language="Portuguese")
            out = client.complete(prompt, [path], profile["model"], profile["settings"])
            parsed = parse_json_response(out.text, [p.id for p in block.pairs])
            pair_for = {p.concept: p.id for p in block.pairs}
            for key in keys:
                a_values[key] = parsed.get(pair_for.get(key))

        # --- arm B: one call per tick group
        b_values = {}
        for group in GROUPS:
            spec = next(g for g in ui["clinical_history"]["groups"] if g["concept"] == group["concept"])
            image = render_strip(alignment.image, spec["strip"], spec["bands"], left=56, font=1.2)
            path = TMP / f"{form_name}_{group['key']}.jpg"
            align.save_jpeg(image, path)
            out = client.complete(prompt_for(group, len(spec["bands"])), [path],
                                  profile["model"], profile["settings"])
            ticked = decode_ticked(parse_json_response(out.text, ["ticked"]))
            b_values[group["key"]] = (group["options"][ticked[0] - 1]
                                      if len(ticked) == 1 and 1 <= ticked[0] <= len(group["options"]) else None)

        # gender via cut-and-stack
        spec = ui["gender"]
        image = stack_bands(alignment.image, spec["strip"], spec["bands"], gap=70, left=56, font=1.2)
        path = TMP / f"{form_name}_gender_stack.jpg"
        align.save_jpeg(image, path)
        out = client.complete(prompt_for({"question": "Sexo", "labels": ["M", "F"], "options": ["M", "F"]}, 2),
                              [path], profile["model"], profile["settings"])
        ticked = decode_ticked(parse_json_response(out.text, ["ticked"]))
        b_values["gender"] = "M" if ticked == [1] else ("F" if ticked == [2] else None)

        for key in ("itching", "leprosy_contact", "previous_leprosy_treatment", "referred_by", "gender"):
            expected = expired.get(key)
            if expected is None:
                continue
            concept = concepts.get(key, {})
            ok_a = normalise.compare("choice_single", a_values.get(key), expected, concept)
            ok_b = normalise.compare("choice_single", b_values.get(key), expected, concept)
            scores["block"].setdefault(key, [0, 0])
            scores["strips"].setdefault(key, [0, 0])
            scores["block"][key][0] += int(bool(ok_a)); scores["block"][key][1] += 1
            scores["strips"][key][0] += int(bool(ok_b)); scores["strips"][key][1] += 1
            rows.append({"form": form_name, "concept": key, "expected": expected,
                         "block": a_values.get(key), "strips": b_values.get(key),
                         "block_ok": bool(ok_a), "strips_ok": bool(ok_b)})

        print(f"{form_name}  block: " + ", ".join(f"{k}={a_values.get(k)}" for k in
              ("itching", "leprosy_contact", "previous_leprosy_treatment", "referred_by", "gender")))
        print(f"         strips: " + ", ".join(f"{k}={b_values.get(k)}" for k in
              ("itching", "leprosy_contact", "previous_leprosy_treatment", "referred_by", "gender")))

    print("\nper concept (strips vs block)")
    for key in ("itching", "leprosy_contact", "previous_leprosy_treatment", "referred_by", "gender"):
        if key in scores["block"]:
            b, bn = scores["block"][key]
            s, sn = scores["strips"][key]
            print(f"  {key:30s} block {b}/{bn}   strips {s}/{sn}")

    Path("output/tick_strip_test.json").write_text(json.dumps(rows, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
