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

_CACHE: dict[tuple[str, str], dict] = {}


def load_spec(country: str = "mozambique", page: str = "front") -> dict:
    key = (country, page)
    if key not in _CACHE:
        name = "tick_columns.yaml" if page in {"front", "draft"} else f"tick_columns.{page}.yaml"
        path = settings.COUNTRIES_DIR / country / name
        _CACHE[key] = yaml.safe_load(path.read_text()) if path.exists() else {}
    return _CACHE[key]


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
    for group in (spec.get("reactions") or {}).get("groups") or []:
        out.append({**group, "kind": group.get("kind", "horizontal")})
    if spec.get("outcome"):
        out.append({**spec["outcome"], "kind": "vertical"})
    return out


def prompt(group: dict) -> str:
    labels = group["labels"]
    bands = "; ".join(f'band {i + 1} = "{label}"' for i, label in enumerate(labels))
    layout = "side by side, left to right" if group.get("kind") == "horizontal" else "top to bottom"
    return (
        f"This image shows {len(labels)} numbered tickboxes from a leprosy clinic card, "
        f"arranged {layout} in the order they are printed.\n"
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
    font_scale = float(opts.get("number_scale", 1.2))
    thick = int(opts.get("number_thickness", 3))
    gap = int(opts.get("stack_gap_px", 110))
    if group.get("kind") == "horizontal":
        return stack_bands(image, group["strip"], group["bands"], gap=gap, left=left,
                           pad=pad, font=font_scale, thickness=thick,
                           separator=bool(opts.get("band_separator", True)))
    return render_strip(image, group["strip"], group["bands"], left=left, pad=pad,
                        font=font_scale, thickness=thick)


def reaction_prompt(start: int, end: int) -> str:
    return (
        f"This image shows rows {start} to {end} from a leprosy clinic card, stacked "
        "with even spacing and numbered in the left margin. Each row has two printed "
        "boxes side by side: left = SIM, right = NÃO.\n"
        "A box is ticked when there is a handwritten tick, cross or filled mark inside "
        "it. An empty printed box is not ticked. If neither box in a row is marked, "
        "that row is blank.\n"
        "For every row number, answer exactly one of: \"sim\", \"não\", \"blank\".\n"
        'Return one JSON object and nothing else, for example '
        '{"1": "sim", "2": "não", "3": "blank"}.'
    )


def _read_reaction_grid(aligned, groups_list, client, profile, run_dir, opts, fallback=None):
    """Two VLM calls: rows 1–5 and 6–10, each as a regular numbered stack."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.parse import parse_json_response
    from tools.render_tick_strip import render_rows

    values: dict[str, dict] = {}
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0, "errors": []}
    left = int(opts.get("left_margin_px", 80))
    pad = int(opts.get("border_px", 12))
    gap = int(opts.get("reaction_gap_px", opts.get("stack_gap_px", 20)))
    font_scale = float(opts.get("number_scale", 1.2))
    image_scale = float(opts.get("reaction_scale", 2.5))
    inset = int(opts.get("reaction_inset_px", 3))
    headers = ("SIM", "NÃO") if opts.get("reaction_headers", True) else None
    thick = int(opts.get("number_thickness", 3))

    def one(start: int, end: int):
        rows = [group["strip"] for group in groups_list[start:end]]
        image = render_rows(
            aligned, rows, left=left, pad=pad, gap=gap,
            font=font_scale, thickness=thick, start=start + 1,
            inset=inset, scale=image_scale, headers=headers,
        )
        threshold = int(opts.get("reaction_threshold", 0) or 0)
        if threshold:
            # Remove the printed grey box outline; keep the dark handwritten mark.
            # The VLM still makes the yes/no/blank choice from the cleaned layout.
            grey = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
            _, binary = cv2.threshold(grey, threshold, 255, cv2.THRESH_BINARY)
            image = cv2.cvtColor(binary, cv2.COLOR_GRAY2BGR)
        path = run_dir / f"tick_reactions_{start + 1}_{end}.jpg"
        cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        try:
            try:
                completion = client.complete(
                    reaction_prompt(start + 1, end), [path], profile["model"],
                    profile["settings"],
                )
            except Exception:
                if not fallback:
                    raise
                fb_client, fb_profile = fallback
                completion = fb_client.complete(
                    reaction_prompt(start + 1, end), [path], fb_profile["model"],
                    fb_profile["settings"],
                )
            parsed = parse_json_response(
                completion.text, [str(n) for n in range(start + 1, end + 1)]
            )
            entries = {}
            for n, group in enumerate(groups_list[start:end], start + 1):
                answer = str(parsed.get(str(n)) or "").strip().lower()
                if answer in {"sim", "s", "yes", "left"}:
                    value = group["options"][0]
                elif answer in {"não", "nao", "n", "no", "right"}:
                    value = group["options"][1]
                else:
                    value = None
                entries[group["concept"]] = {
                    "value": value,
                    "band": None,
                    "raw": f"row {n} = {answer or 'blank'}",
                }
            return entries, {
                "input_tokens": completion.input_tokens or 0,
                "output_tokens": completion.output_tokens or 0,
                "cost_usd": completion.cost_usd or 0.0,
            }, None
        except Exception as exc:
            return {
                group["concept"]: {"value": None, "band": None, "raw": None}
                for group in groups_list[start:end]
            }, None, f"reaction rows {start + 1}-{end}: {exc}"

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(one, start, start + 5) for start in (0, 5)]
        for future in as_completed(futures):
            entries, used, error = future.result()
            values.update(entries)
            usage["calls"] += 1
            if used:
                usage["input_tokens"] += used["input_tokens"]
                usage["output_tokens"] += used["output_tokens"]
                usage["cost_usd"] += used["cost_usd"]
            if error:
                usage["errors"].append(error)
    return values, usage


def _read_one_strip(aligned, group, opts, client, profile, run_dir, fallback=None):
    from core.parse import parse_json_response

    concept = group["concept"]
    image = render(aligned, group, opts)
    path = run_dir / f"tick_{concept}.jpg"
    cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    try:
        try:
            completion = client.complete(
                prompt(group), [path], profile["model"], profile["settings"]
            )
        except Exception:
            # A rate-limited primary is the common failure. The fallback model
            # reads the same strip once; a strip is one question, so this is
            # cheap, and a null tick is a blocking "missing" field downstream.
            if not fallback:
                raise
            fb_client, fb_profile = fallback
            completion = fb_client.complete(
                prompt(group), [path], fb_profile["model"], fb_profile["settings"]
            )
        parsed = parse_json_response(completion.text, ["ticked"])
        ticked = decode(parsed.get("ticked") if isinstance(parsed, dict) else parsed)
        if len(ticked) == 1 and 1 <= ticked[0] <= len(group["labels"]):
            entry = {
                "value": group["options"][ticked[0] - 1],
                "band": ticked[0],
                "raw": f"band {ticked[0]}",
            }
        else:
            entry = {"value": None, "band": None, "raw": f"bands {ticked}"}
        return concept, entry, {
            "input_tokens": completion.input_tokens or 0,
            "output_tokens": completion.output_tokens or 0,
            "cost_usd": completion.cost_usd or 0.0,
        }, None
    except Exception as exc:
        return concept, {"value": None, "band": None, "raw": None}, None, f"tick {concept}: {exc}"


def read_ticks(
    aligned,
    country: str,
    client,
    profile: dict,
    run_dir: Path,
    page: str = "front",
    fallback=None,
) -> tuple[dict, dict]:
    """Read tick groups. Front uses one strip per question; back uses two 5-row grids."""
    from concurrent.futures import ThreadPoolExecutor, as_completed
    from core.parse import parse_json_response

    spec = load_spec(country, page)
    opts = spec.get("render") or {}
    groups_list = groups(spec)
    if page == "back":
        reaction_groups = [g for g in groups_list if g.get("kind") == "horizontal"]
        other_groups = [g for g in groups_list if g.get("kind") != "horizontal"]
        values, usage = _read_reaction_grid(
            aligned, reaction_groups, client, profile, run_dir, opts, fallback
        )
        for group in other_groups:
            concept, entry, used, error = _read_one_strip(
                aligned, group, opts, client, profile, run_dir, fallback
            )
            values[concept] = entry
            usage["calls"] += 1
            if used:
                usage["input_tokens"] += used["input_tokens"]
                usage["output_tokens"] += used["output_tokens"]
                usage["cost_usd"] += used["cost_usd"]
            if error:
                usage["errors"].append(error)
        return values, usage

    values: dict[str, dict] = {}
    usage = {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0, "calls": 0, "errors": []}

    def one(group: dict) -> tuple[str, dict, dict | None, str | None]:
        return _read_one_strip(aligned, group, opts, client, profile, run_dir, fallback)

    workers = max(1, min(int((profile.get("settings") or {}).get("parallel", 2)), len(groups_list) or 1))
    if groups_list:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(one, group) for group in groups_list]
            for future in as_completed(futures):
                concept, entry, used, error = future.result()
                values[concept] = entry
                usage["calls"] += 1
                if used:
                    usage["input_tokens"] += used["input_tokens"]
                    usage["output_tokens"] += used["output_tokens"]
                    usage["cost_usd"] += used["cost_usd"]
                if error:
                    usage["errors"].append(error)
    return values, usage
