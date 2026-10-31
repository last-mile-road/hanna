"""Read the card's tick groups from rendered strips.

The block prompt asks nine fields at once and gets ticks wrong; a strip asks one question
with numbered boxes and gets them right (49/49 against 30/35 on seven cards,
docs/tick-strips-2026-10-30.md).

Geometry and printed labels live in `countries/<country>/tick_columns.yaml`; the drawing is
`tools/render_tick_strip.py`, so the benchmark and production share one code path.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import cv2
import yaml

from . import settings

_CACHE: dict[str, dict] = {}


def load_spec(country: str = "mozambique") -> dict:
    if country not in _CACHE:
        path = settings.COUNTRIES_DIR / country / "tick_columns.yaml"
        _CACHE[country] = yaml.safe_load(path.read_text()) if path.exists() else {}
    return _CACHE[country]


def groups(spec: dict) -> list[dict]:
    """Flatten the spec into one entry per printed question."""
    out: list[dict] = []
    for group in (spec.get("clinical_history") or {}).get("groups") or []:
        out.append({**group, "kind": "vertical"})
    for key, section in (("case_category", "case_category"),
                         ("leprosy_classification", "leprosy_classification")):
        block = spec.get(section)
        if block:
            out.append({**block, "concept": key, "kind": "vertical"})
    if spec.get("gender"):
        out.append({**spec["gender"], "concept": "gender", "kind": "horizontal"})
    return out


def prompt(group: dict) -> str:
    labels = group["labels"]
    bands = "; ".join(f'band {i + 1} = "{label}"' for i, label in enumerate(labels))
    return (
        f"This image shows {len(labels)} numbered tickboxes from a leprosy clinic card, "
        f"stacked in the order they are printed.\n"
        f"Question on the card: \"{group['question']}\"\n{bands}.\n"
        "A band is ticked when there is a handwritten tick, cross or filled mark inside its "
        "box. An empty printed box is not ticked.\n"
        "Return one JSON object and nothing else: {\"ticked\": [<band numbers>]}. "
        "Use an empty list when no band is clearly marked."
    )


def decode(ticked: Any) -> list[int]:
    if isinstance(ticked, list):
        out = []
        for item in ticked:
            try:
                out.append(int(item))
            except (TypeError, ValueError):
                continue
        return out
    if isinstance(ticked, int):
        return [ticked]
    if isinstance(ticked, str):
        return [int(part) for part in ticked.replace(",", " ").split() if part.isdigit()]
    return []


def render(image, group: dict, render_opts: dict | None = None):
    """Return the strip image the model will see."""
    from tools.render_tick_strip import render_strip, stack_bands

    opts = render_opts or {}
    left = int(opts.get("left_margin_px", 56))
    pad = int(opts.get("border_px", 12))
    scale = float(opts.get("number_scale", 1.2))
    thick = int(opts.get("number_thickness", 3))
    gap = int(opts.get("stack_gap_px", 70))
    if group.get("kind") == "horizontal":
        return stack_bands(image, group["strip"], group["bands"], gap=gap, left=left,
                           pad=pad, font=scale, thickness=thick)
    return render_strip(image, group["strip"], group["bands"], left=left, pad=pad,
                        font=scale, thickness=thick)


def read_ticks(aligned, country: str, client, profile: dict, run_dir: Path) -> tuple[dict, dict]:
    """One call per group. Returns {concept: {...}}, plus tokens/cost totals and errors."""
    from core.parse import parse_json_response

    spec = load_spec(country)
    opts = spec.get("render") or {}
    values: dict[str, dict] = {}
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0, "errors": []}

    for group in groups(spec):
        concept = group["concept"]
        image = render(aligned, group, opts)
        path = run_dir / f"tick_{concept}.jpg"
        cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        try:
            completion = client.complete(prompt(group), [path], profile["model"], profile["settings"])
            parsed = parse_json_response(completion.text, ["ticked"])
            ticked = decode(parsed.get("ticked") if isinstance(parsed, dict) else parsed)
            if len(ticked) == 1 and 1 <= ticked[0] <= len(group["labels"]):
                values[concept] = {
                    "value": group["options"][ticked[0] - 1],
                    "band": ticked[0],
                    "raw": f"band {ticked[0]}",
                }
            else:
                values[concept] = {"value": None, "band": None, "raw": f"bands {ticked}"}
            usage["input_tokens"] += completion.input_tokens or 0
            usage["output_tokens"] += completion.output_tokens or 0
            usage["cost_usd"] += completion.cost_usd or 0.0
            usage["calls"] += 1
        except Exception as exc:  # a strip that fails keeps the block's value
            values[concept] = {"value": None, "band": None, "raw": None}
            usage["errors"].append(f"tick {concept}: {exc}")
    return values, usage
