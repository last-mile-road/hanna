"""Run one card through one profile and write the evidence to the ledger."""

from __future__ import annotations

import csv
import hashlib
import json
import re
import time
from datetime import date
from concurrent.futures import ThreadPoolExecutor, as_completed

import cv2
import numpy as np
import yaml
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, text, update

from . import align, aliases, location, normalise, settings, store, ticks
from .db import connect
from .forms import Block, Pair
from .inference import OpenRouter
from .parse import parse_json_response
from .prompts import build_prompt, prompt_hash
from .readers import read_block as read_table_block
from .treatment import derive_treatment_grid
from .validate import validate_run
from .schema import block_runs, cards, forms, observations, profiles, runs

OCR_BLOCK_TYPES = {"value_pairs", "tick_grid", "annotated_table", "free_text"}
INK_MIN = 0.008  # share of dark ink pixels not present in the blank template

# A profile id that is not a model: those blocks are read by MinerU on the box.
MINERU_PROFILE = "mineru-standard"


def _complete_with_fallback(client_for, prompt, image_paths, profile, fallback):
    """Ordered fail-through: one model rate-limited upstream must not fail the block.

    `client_for` returns the client for a profile's provider, so the primary can
    be the self-hosted box and the fallback OpenRouter (or the reverse) — the
    fail-over crosses providers, not just model names.
    """
    try:
        return (
            client_for(profile).complete(
                prompt, image_paths, profile["model"], profile["settings"]
            ),
            profile["id"],
        )
    except Exception:
        if not fallback or fallback["id"] == profile["id"]:
            raise
        return (
            client_for(fallback).complete(
                prompt, image_paths, fallback["model"], fallback["settings"]
            ),
            fallback["id"],
        )


def _block_ink(aligned, template, bbox, darkness: int = 120) -> float:
    """Share of dark ink pixels in a block that the blank template does not have.

    A raw absolute difference is dominated by the photo's paper tone; the adaptive
    mask removes paper texture and the darkness floor keeps shadows out. The
    template mask is dilated to absorb a few pixels of alignment error.
    """
    form_crop = align.crop_block(aligned, bbox)
    template_crop = align.crop_block(template, bbox)
    if form_crop.shape[:2] != template_crop.shape[:2]:
        template_crop = cv2.resize(
            template_crop, (form_crop.shape[1], form_crop.shape[0])
        )
    form_gray = cv2.cvtColor(form_crop, cv2.COLOR_BGR2GRAY)
    template_gray = cv2.cvtColor(template_crop, cv2.COLOR_BGR2GRAY)
    form_adaptive = (
        cv2.adaptiveThreshold(
            form_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 15, 7,
        ) > 0
    )
    template_adaptive = (
        cv2.adaptiveThreshold(
            template_gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 15, 7,
        ) > 0
    )
    form_ink = ((form_gray < darkness) & form_adaptive).astype(np.uint8)
    template_ink = ((template_gray < darkness) & template_adaptive).astype(np.uint8)
    template_ink = cv2.dilate(
        template_ink, np.ones((5, 5), np.uint8), iterations=1
    )
    new_ink = cv2.bitwise_and(form_ink, cv2.bitwise_not(template_ink))
    return float(new_ink.mean())


def _skip_empty_visits(blocks, aligned, template):
    """Drop the six blocks of a visit column whose header has no ink.

    The back card prints four visit columns from the start. A card with one
    completed visit should cost one visit, not four; the blank headers are
    detected against the template before any model call.
    """
    by_visit: dict[str, list] = {}
    for block in blocks:
        match = re.match(r"visit(\d+)_", block.id)
        if match:
            by_visit.setdefault(match.group(1), []).append(block)

    empty: set[str] = set()
    for visit, visit_blocks in by_visit.items():
        inks = [
            _block_ink(aligned, template, block.bbox)
            for block in visit_blocks
            if block.bbox
        ]
        if inks and max(inks) < INK_MIN:
            empty.add(visit)
    if empty:
        print(f"  visit columns with no ink: {', '.join(sorted(empty))}")

    kept = []
    for block in blocks:
        match = re.match(r"visit(\d+)_", block.id)
        if match is None or match.group(1) not in empty:
            kept.append(block)
    return kept


def _find_card(conn, form_name: str):
    return conn.execute(
        select(cards).where(cards.c.image_path.like(f"%/{form_name}.jpg"))
    ).mappings().one()


def _find_card_by_id(conn, card_id: int):
    return conn.execute(select(cards).where(cards.c.id == card_id)).mappings().one()


def _match_option(text, pair) -> str:
    key = normalise.key(text)
    for option in pair.options or []:
        if normalise.key(option) == key:
            return str(option)
    return str(text)


def _apply_box_states(parsed: dict, pairs_in_block) -> None:
    """Use the per-box states when they are unambiguous.

    The model answers `<pair>_boxes` with every printed option marked ticked or unticked.
    Exactly one ticked box sets the value, whatever the model put in the value field; zero
    or two ticked boxes leave its answer alone, so the ambiguity reaches the reviewer
    instead of being resolved inside the prompt.
    """
    for pair in pairs_in_block:
        states = parsed.get(f"{pair.id}_boxes")
        if not isinstance(states, dict):
            continue
        ticked = [
            option for option, mark in states.items()
            if str(mark).strip().lower() in {"ticked", "checked", "x", "yes", "sim", "true", "1"}
        ]
        if len(ticked) == 1:
            parsed[pair.id] = _match_option(ticked[0], pair)


def _find_form(conn, form_id=None, country_id=None, variant: str = "draft"):
    """The form a run works on. Never the globally latest by accident.

    `form_id` wins (a queued run names its form). Otherwise the latest
    published form for the card's country and the requested variant.
    """
    if form_id is not None:
        return conn.execute(select(forms).where(forms.c.id == form_id)).mappings().one()
    query = select(forms).order_by(forms.c.version.desc())
    if country_id is not None:
        query = query.where(forms.c.country_id == country_id)
    rows = conn.execute(query).mappings().all()
    for row in rows:
        if str((row["definition"] or {}).get("variant") or "draft") == variant:
            return row
    if rows:
        return rows[0]
    return conn.execute(
        select(forms).order_by(forms.c.version.desc()).limit(1)
    ).mappings().one()


def _profile(conn, profile_id: str):
    return conn.execute(select(profiles).where(profiles.c.id == profile_id)).mappings().one()


def _profiles(conn) -> dict[str, dict]:
    return {
        row["id"]: row
        for row in conn.execute(select(profiles)).mappings()
    }


def _routing(form: dict, profile_id: str, filename: str = "routing.yaml") -> dict:
    path = settings.COUNTRIES_DIR / form["definition"].get("country", "") / filename
    if not path.exists():
        return {"default": profile_id, "blocks": {}}
    data = yaml.safe_load(path.read_text()) or {}
    data.setdefault("default", profile_id)
    data.setdefault("blocks", {})
    return data


def _label_norm(text: str) -> str:
    """Lowercase, accent-free, punctuation-trimmed form of a label or value."""
    import unicodedata

    folded = unicodedata.normalize("NFD", str(text).lower())
    return "".join(ch for ch in folded if not unicodedata.combining(ch)).strip(" :.-")


def _jsonable(value: Any) -> Any:
    if isinstance(value, Path):
        return str(value)
    return value


def run_card(
    form_name: str | None = None,
    profile_id: str = "or-qwen3vl-8b-v1",
    purpose: str = "slice",
    only_blocks: list[str] | None = None,
    routing: bool = False,
    card_id: int | None = None,
    run_id=None,
    routing_file: str | None = None,
    variant: str = "draft",
) -> dict[str, Any]:
    """Run one card. CLI benchmarks pass `form_name`; the worker passes `card_id` and a
    pre-created `run_id`, so the HTTP layer can hand out the id before the work starts."""
    with connect() as conn:
        card = _find_card_by_id(conn, card_id) if card_id else _find_card(conn, form_name)
        if run_id is not None:
            run_row = conn.execute(select(runs).where(runs.c.id == run_id)).mappings().one()
            form = _find_form(conn, run_row["form_id"])
        else:
            form = _find_form(conn, country_id=card["country_id"], variant=variant)
        profiles_by_id = _profiles(conn)
        profile = profiles_by_id[profile_id]
        routing_map = (
            _routing(form, profile_id, routing_file or settings.ROUTING_FILE)
            if routing
            else {"default": profile_id, "blocks": {}}
        )
        run_profile_id = "or-routing-v1" if routing else profile_id
        fallback_profile = profiles_by_id.get(routing_map.get("fallback", "")) if routing else None
        blocks, pairs, concepts = store.form_rows(conn, form["id"])
        truth = store.ground_truth_map(conn, card["id"], version=1)
        if run_id is None:
            run_id = store.create_run(
                conn, card["id"], form["id"], form["version"], run_profile_id, purpose
            )
        else:  # created by the API as queued; the worker owns it from here
            conn.execute(update(runs).where(runs.c.id == run_id).values(status="running"))
        conn.commit()

    form_name = form_name or Path(card["image_path"]).stem

    # A retried job must not collide with the blocks the failed attempt left
    # behind (uq_block_runs_run_block). Clear this run's evidence and re-read.
    with connect() as conn:
        conn.execute(delete(observations).where(observations.c.run_id == run_id))
        conn.execute(delete(block_runs).where(block_runs.c.run_id == run_id))
        conn.commit()

    run_dir = settings.OUTPUT_DIR / "runs" / str(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    result: dict[str, Any] = {
        "run_id": str(run_id),
        "form": form_name,
        "profile": run_profile_id,
        "blocks": [],
        "judged": 0,
        "correct": 0,
        "input_tokens": 0,
        "output_tokens": 0,
        "cost_usd": 0.0,
        "errors": [],
    }

    _clients: dict[str, OpenRouter] = {}
    table_warnings: list[dict] = []

    def client_for(profile) -> OpenRouter:
        """One client per provider: the box for modal profiles, the hosted
        gateway for everything else. The routing decides which block uses which."""
        provider = str((profile or {}).get("provider") or "openrouter")
        if provider not in _clients:
            if provider == "modal":
                if not settings.MODAL_BOX_URL:
                    raise RuntimeError("MODAL_BOX_URL is not set")
                _clients[provider] = OpenRouter(
                    api_key=settings.MODAL_BOX_KEY, base_url=settings.MODAL_BOX_URL
                )
            else:
                _clients[provider] = OpenRouter()
        return _clients[provider]
    block_hashes: list[str] = []

    try:
        align_started = time.monotonic()
        alignment = align.align_card(Path(card["image_path"]), Path(form["template_path"]))
        alignment_ms = int((time.monotonic() - align_started) * 1000)
        align.save_jpeg(alignment.image, run_dir / "aligned.jpg")
        result["good_matches"] = alignment.good_matches
        result["alignment_ms"] = alignment_ms

        reference_year = int(form["definition"].get("reference_year") or date.today().year)
        # Tick strips are measured on the front template. The back page has its own
        # layout and no strip spec, so running them there would read the wrong pixels.
        form_variant = str((form["definition"] or {}).get("variant") or "draft")
        age_derived: dict[str, int] = {}
        selected = [
            b for b in blocks
            if (not only_blocks or b.id in only_blocks) and b.pairs
        ]
        # The ink filter was unreliable on the first real back photo (print and paper
        # tone produced false ink). The wide visit index is the filled/empty signal;
        # review hides a visit section whose date is blank and whose values are all
        # null. The planner will use the visit index to skip reads later.
        prepared: list[dict[str, Any]] = []
        all_values: dict[str, Any] = {}
        for block in selected:
            block_profile = profiles_by_id[
                routing_map["blocks"].get(block.id, routing_map["default"])
            ]
            pairs_in_block = block.pairs
            crop = align.crop_block(alignment.image, block.bbox)
            raw_path = run_dir / f"block_{block.id}_raw.jpg"
            align.save_jpeg(crop, raw_path)
            send = crop
            image_path = raw_path
            # The table readers (MinerU) want the raw colour crop. The adaptive
            # threshold used for VLM text blocks makes the local MinerU treat the
            # table as a figure and return a base64 image instead of a table.
            is_table_reader = block_profile["id"] == MINERU_PROFILE
            if block.block_type in OCR_BLOCK_TYPES and not is_table_reader:
                send = align.ocr_preprocess(crop)
                image_path = run_dir / f"block_{block.id}_ocr.jpg"
                align.save_jpeg(send, image_path)

            upscale = int((block_profile["settings"] or {}).get("upscale", 1))
            if upscale > 1:
                send = cv2.resize(
                    send, None, fx=upscale, fy=upscale, interpolation=cv2.INTER_CUBIC
                )
                image_path = run_dir / f"block_{block.id}_up{upscale}.jpg"
                align.save_jpeg(send, image_path)

            image_paths = [image_path]
            with_template = (
                block_profile["crop_strategy"] == "block_plus_template"
                and block.block_type == "visual_annotation"
            )
            if with_template:
                template_crop = align.crop_block(alignment.template, block.bbox)
                template_path = run_dir / f"block_{block.id}_template.jpg"
                align.save_jpeg(template_crop, template_path)
                image_paths.append(template_path)

            prompt = build_prompt(
                block,
                pairs_in_block,
                concepts,
                country=form["definition"].get("country", "Mozambique").title(),
                language=form["definition"].get("language", "Portuguese"),
                with_template=with_template,
            )
            p_hash = prompt_hash(prompt, block_profile["model"], block_profile["settings"])
            block_hashes.append(p_hash)
            prepared.append(
                {
                    "block": block,
                    "pairs": pairs_in_block,
                    "prompt": prompt,
                    "image_paths": image_paths,
                    "p_hash": p_hash,
                    "profile": block_profile,
                    "block_values": {
                        "run_id": run_id,
                        "block_id": block.id,
                        "block_type": block.block_type,
                        "profile_id": block_profile["id"],
                        "prompt_hash": p_hash,
                        "prompt": prompt,
                        "status": "running",
                        "crop_path": str(image_path),
                    },
                    "completion": None,
                    "error": None,
                    "ink": _block_ink(alignment.image, alignment.template, block.bbox),
                }
            )

        groups: dict[str, list[dict]] = {}
        for item in prepared:
            groups.setdefault(item["profile"]["id"], []).append(item)

        def record(item: dict) -> None:
            block = item["block"]
            pairs_in_block = item["pairs"]
            block_values = item["block_values"]
            if item.get("used_profile"):
                # The ledger names the model that actually answered, including
                # a 9B -> 8B fallback. No silent substitution.
                block_values["profile_id"] = item["used_profile"]
            completion = item["completion"]
            if completion is not None:
                block_values.update(
                    raw_response=completion.text,
                    status="done",
                    latency_ms=completion.latency_ms,
                    input_tokens=completion.input_tokens,
                    output_tokens=completion.output_tokens,
                )
                parsed = parse_json_response(
                    completion.text, [p.id for p in pairs_in_block]
                )
                blank_by_ink = (
                    not item.get("reader")
                    and float(item.get("ink", 1.0)) < INK_MIN
                )
                if blank_by_ink:
                    # No handwriting in this block. Printed options are not
                    # answers; drop anything the model invented from them.
                    parsed = {}
                if not parsed and pairs_in_block and not item.get("reader") and not blank_by_ink:
                    # An empty answer is not a result. One retry, then say so out loud:
                    # the 13:48 card lost its whole demographics block this way and looked
                    # like a completed run with every field blank.
                    try:
                        retry = client_for(item["profile"]).complete(
                            item["prompt"],
                            item["image_paths"],
                            item["profile"]["model"],
                            item["profile"]["settings"],
                        )
                        parsed = parse_json_response(
                            retry.text, [p.id for p in pairs_in_block]
                        )
                        _apply_box_states(parsed, pairs_in_block)
                        if parsed:
                            block_values["retried"] = True
                            result["input_tokens"] += retry.input_tokens or 0
                            result["output_tokens"] += retry.output_tokens or 0
                            result["cost_usd"] += retry.cost_usd or 0.0
                    except Exception as retry_error:  # keep the first answer visible
                        item["error"] = f"retry failed: {retry_error}"
                if block.block_type == "dose_grid" and parsed:
                    parsed.update(derive_treatment_grid(parsed))
                if block.id == "self_care" and isinstance(
                    parsed.get("self_care_group"), str
                ):
                    printed = _label_norm(parsed["self_care_group"])
                    if printed in {
                        "auto cuidados",
                        "grupo de auto cuidados",
                        "integrado num grupo de auto cuidados",
                    }:
                        parsed["self_care_group"] = None
                if block.id == "hand_feet_assessment":
                    # The model often reads the normal-sensation ticks as values
                    # (all counts 0) yet still calls the section empty. A section
                    # with any read value is filled; the human can still confirm
                    # it in review if every value is blank.
                    others = [
                        parsed.get(pair.id) for pair in pairs_in_block
                        if pair.id != "is_filled"
                    ]
                    if any(
                        value is not None and str(value).strip() != ""
                        for value in others
                    ):
                        parsed["is_filled"] = "true"
                _apply_box_states(parsed, pairs_in_block)
                if not parsed and pairs_in_block:
                    if item.get("reader") or blank_by_ink:
                        # A blank table or a block with no handwriting is a valid
                        # empty read, not a failure.
                        block_values["status"] = "done"
                    else:
                        block_values["status"] = "empty"
                        result["errors"].append(f"{block.id}: empty response")
                result["input_tokens"] += completion.input_tokens or 0
                result["output_tokens"] += completion.output_tokens or 0
                result["cost_usd"] += completion.cost_usd or 0.0
            else:
                parsed = {}
                block_values.update(status="failed", error=item["error"])
                result["errors"].append(f"{block.id}: {item['error']}")

            with connect() as conn:
                block_run_id = store.add_block_run(conn, block_values)
                observation_rows = []
                block_correct = 0
                block_judged = 0
                for pair in pairs_in_block:
                    concept = concepts[pair.concept]
                    raw_value = parsed.get(pair.id)
                    # Never store a printed label as a value: the reader sometimes
                    # returns "Preenchido por:" for an empty line. If the read is
                    # the field's own label (or its WHO name), it is blank.
                    if isinstance(raw_value, str):
                        seen = _label_norm(raw_value)
                        if seen and seen in {_label_norm(pair.label_local or ""),
                                             _label_norm(concept.get("who_name") or "")}:
                            raw_value = None
                    got = normalise.canonical(
                        pair.value_type, raw_value, concept, reference_year=reference_year
                    )
                    if pair.value_type == "date_or_age":
                        age = normalise.age_from_text(raw_value)
                        if age is not None:
                            age_derived[pair.id] = age
                    all_values[pair.id] = got
                    expected_row = truth.get(pair.id)
                    expected = expected_row["value"] if expected_row else None
                    correct = (
                        normalise.compare(pair.value_type, got, expected, concept)
                        if expected_row
                        else None
                    )
                    if correct is not None:
                        block_judged += 1
                        block_correct += int(correct)
                    observation_rows.append(
                        {
                            "run_id": run_id,
                            "block_run_id": block_run_id,
                            "block_id": block.id,
                            "pair_id": pair.id,
                            "concept_id": pair.concept,
                            "value": got,
                            "raw_value": None if raw_value is None else str(raw_value),
                            "expected_value": expected,
                            "correct": correct,
                        }
                    )
                store.add_observations(conn, observation_rows)
                conn.commit()

            result["judged"] += block_judged
            result["correct"] += block_correct
            result["blocks"].append(
                {
                    "block": block.id,
                    "type": block.block_type,
                    "status": block_values["status"],
                    "judged": block_judged,
                    "correct": block_correct,
                    "latency_ms": block_values.get("latency_ms"),
                    "input_tokens": block_values.get("input_tokens"),
                    "output_tokens": block_values.get("output_tokens"),
                    "parsed_fields": len(parsed),
                    "fields": len(pairs_in_block),
                }
            )
            print(
                f"  {block.id:22s} {block_values['status']:6s} "
                f"{block_correct}/{block_judged} "
                f"{block_values.get('latency_ms', '-')} ms"
            )

        def read_table_checked(item: dict):
            """Read a table once; retry at 2x when ink is present but cells are missing.

            MinerU is fast. The retry is for the failure mode the owner saw:
            handwriting is there, but a row or column was dropped. Two different
            complete reads are surfaced for review, never silently averaged.
            """
            block = item["block"]
            path = Path(item["image_paths"][0])
            expected = len([pair for pair in item["pairs"] if not pair.derived])
            ink = _block_ink(alignment.image, alignment.template, block.bbox)
            first_error = None
            try:
                reading = read_table_block(path, block.id)
            except Exception as exc:
                if ink < INK_MIN:
                    raise
                reading = None
                first_error = exc
            parsed = json.loads(reading.text or "{}") if reading is not None else {}
            if len(parsed) >= expected or ink < INK_MIN:
                return reading

            retry_path = run_dir / f"block_{block.id}_retry2x.jpg"
            image = cv2.imread(str(path))
            retry = None
            if image is not None:
                image = cv2.resize(
                    image, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC
                )
                align.save_jpeg(image, retry_path)
                try:
                    retry = read_table_block(retry_path, block.id)
                except Exception:
                    retry = None
            if retry is None:
                if reading is None and first_error is not None:
                    raise first_error
                return reading
            parsed_retry = json.loads(retry.text or "{}")
            if len(parsed_retry) > len(parsed):
                reading = retry
                parsed = parsed_retry
            if (
                parsed
                and parsed_retry
                and parsed != parsed_retry
                and len(parsed) == len(parsed_retry)
                and not block.id.startswith("visit")
            ):
                table_warnings.append(
                    {
                        "block_id": block.id,
                        "pair_id": None,
                        "message": (
                            "Duas leituras MinerU diferentes para esta tabela. "
                            "Confirme as células assinaladas e digitalize de novo."
                        ),
                    }
                )
            return reading

        def process_group(items):
            """One profile group: its blocks in parallel, then persist each.

            Groups run concurrently with each other (below), so a slow VLM group
            no longer holds the table readers back — the box starts working at
            the same time as the first model call, not after the last one.
            """
            group_profile = items[0]["profile"]
            is_reader = group_profile["id"] == MINERU_PROFILE
            workers = max(
                1,
                min(
                    int((group_profile["settings"] or {}).get("parallel", 4)),
                    len(items),
                ),
            )
            with ThreadPoolExecutor(max_workers=workers) as pool:
                if is_reader:
                    for item in items:
                        item["reader"] = True
                    futures = {
                        pool.submit(read_table_checked, item): item
                        for item in items
                    }
                else:
                    futures = {
                        pool.submit(
                            _complete_with_fallback,
                            client_for,
                            item["prompt"],
                            item["image_paths"],
                            group_profile,
                            fallback_profile,
                        ): item
                        for item in items
                    }
                for future in as_completed(futures):
                    item = futures[future]
                    try:
                        result = future.result()
                        if item.get("reader"):
                            item["completion"] = result
                            item["used_profile"] = item["profile"]["id"]
                        else:
                            item["completion"], item["used_profile"] = result
                    except Exception as exc:
                        # A controlled reader that fails is surfaced, not silently
                        # replaced: the block fails and the run says so.
                        item["error"] = str(exc)
                    # Persist as each block lands: SSE reads block_runs, so a late
                    # write would turn progress into one batch at the end.
                    record(item)

        if len(groups) <= 1:
            for items in groups.values():
                process_group(items)
        else:
            with ThreadPoolExecutor(max_workers=len(groups)) as group_pool:
                futures = [group_pool.submit(process_group, items) for items in groups.values()]
                for future in futures:
                    future.result()

        # Tick groups: one strip per printed question, overriding the block values for the
        # tick fields. Measured 49/49 for strips against 30/35 for the block prompt.
        tick_values: dict = {}
        if routing:
            tick_profile_id = routing_map.get("strips", profile_id)
            tick_profile = profiles_by_id.get(tick_profile_id, profile)
            try:
                tick_values, tick_usage = ticks.read_ticks(
                    alignment.image,
                    str(form["definition"].get("country", "mozambique")).lower(),
                    client_for(tick_profile),
                    tick_profile,
                    run_dir,
                    page=form_variant,
                )
                result["input_tokens"] += tick_usage["input_tokens"]
                result["output_tokens"] += tick_usage["output_tokens"]
                result["cost_usd"] += tick_usage["cost_usd"]
                result["tick_calls"] = tick_usage["calls"]
                for note in tick_usage["errors"]:
                    print("  warning:", note)
            except Exception as exc:  # strips are an improvement, not a hard dependency
                print("  warning: tick strips failed:", exc)

        if tick_values:
            spec_groups = {
                g["concept"]: g
                for g in ticks.groups(
                    ticks.load_spec(
                        str(form["definition"].get("country", "mozambique")).lower(),
                        form_variant,
                    )
                )
            }
            for concept, entry in tick_values.items():
                pair_id = (spec_groups.get(concept) or {}).get("pair")
                if not pair_id:
                    continue
                all_values[pair_id] = entry["value"]
                pair = next((item for item in pairs if item.id == pair_id), None)
                expected_row = truth.get(pair_id)
                expected = expected_row["value"] if expected_row else None
                correct = (
                    normalise.compare(pair.value_type, entry["value"], expected, concepts[pair.concept])
                    if (pair and expected is not None)
                    else None
                )
                with connect() as conn:
                    conn.execute(
                        text(
                            "UPDATE observations SET value = :value, raw_value = :raw, correct = :correct "
                            "WHERE run_id = :run_id AND pair_id = :pair_id"
                        ),
                        {"value": entry["value"], "raw": entry["raw"], "correct": correct,
                         "run_id": run_id, "pair_id": pair_id},
                    )
                    conn.commit()

        # The back REACÇÕES section is ten signs; the headline the clinician needs
        # is "was a reaction diagnosed". Derive it from the signs, not a new model
        # call, and keep the signs as the evidence.
        reaction_pairs = [
            pair for pair in pairs
            if pair.block_id in {"reactions_history", "reactions_exam"} and not pair.derived
        ]
        reaction_values = [all_values.get(pair.id) for pair in reaction_pairs]
        reaction_known = any(
            value is not None and str(value).strip() != ""
            for value in reaction_values
        )
        reaction_positive = any(
            str(value).strip().lower() in {"true", "yes", "sim", "+"}
            for value in reaction_values
        )
        reaction_summary = (
            "True" if reaction_positive else ("False" if reaction_known else None)
        )
        if any(pair.id == "reaction_diagnosed" for pair in pairs):
            all_values["reaction_diagnosed"] = reaction_summary
            with connect() as conn:
                conn.execute(
                    text(
                        "UPDATE observations SET value = :value, raw_value = :value "
                        "WHERE run_id = :run_id AND pair_id = 'reaction_diagnosed'"
                    ),
                    {"value": reaction_summary, "run_id": run_id},
                )
                conn.commit()

        # An age is as-of the visit, not as-of today. Once the diagnosis date is known,
        # re-anchor every age-derived birth year to it and keep the observation honest.
        diagnosis = all_values.get("date_diagnosed")
        diagnosis_year = None
        if diagnosis and str(diagnosis)[:4].isdigit():
            diagnosis_year = int(str(diagnosis)[:4])
        if diagnosis_year and age_derived:
            for pair_id, age in age_derived.items():
                corrected_year = str(diagnosis_year - age)
                if all_values.get(pair_id) == corrected_year:
                    continue
                all_values[pair_id] = corrected_year
                expected_row = truth.get(pair_id)
                expected = expected_row["value"] if expected_row else None
                concept = concepts.get(pair_id) or {}
                for pair in pairs:
                    if pair.id == pair_id:
                        concept = concepts[pair.concept]
                        break
                correct = (
                    normalise.compare("date_or_age", corrected_year, expected, concept)
                    if expected is not None
                    else None
                )
                with connect() as conn:
                    conn.execute(
                        text(
                            "UPDATE observations SET value = :value, correct = :correct "
                            "WHERE run_id = :run_id AND pair_id = :pair_id"
                        ),
                        {"value": corrected_year, "correct": correct,
                         "run_id": run_id, "pair_id": pair_id},
                    )
                    conn.commit()

        validation = validate_run(
            blocks,
            pairs,
            concepts,
            all_values,
            country=form["definition"].get("country", "mozambique"),
            as_of=date.today(),
        )
        if table_warnings:
            for warning in table_warnings:
                validation["issues"].append(
                    {
                        "pair_id": warning.get("pair_id"),
                        "block_id": warning.get("block_id"),
                        "kind": "table_inconsistent",
                        "severity": "blocking",
                        "audience": ["clinician"],
                        "advice": warning["message"],
                        "bbox": None,
                    }
                )
            validation["blocking"] = sum(
                1 for issue in validation["issues"] if issue["severity"] == "blocking"
            )
            validation["info"] = sum(
                1 for issue in validation["issues"] if issue["severity"] == "info"
            )
            validation["status"] = "blocked" if validation["blocking"] else (
                "review" if validation["info"] else "ok"
            )
        result["validation"] = validation
        (run_dir / "validation.json").write_text(
            json.dumps(validation, indent=2, ensure_ascii=False)
        )
        print(
            f"  validation: {validation['status']} "
            f"({validation['blocking']} blocking, {validation['info']} info)"
        )

        # Location: gazetteer first, a bounded service second, a human third. Stored with the
        # run so the review can show a map instead of three hidden text fields. Before the
        # gazetteer sees the names, the alias table is consulted: a spelling a reviewer has
        # already resolved resolves exactly this time, with no fuzzy guess.
        written_location = {
            "district": str(all_values.get("district") or ""),
            "locality": str(all_values.get("locality") or ""),
            "village": str(all_values.get("village_name") or ""),
        }
        alias_hits = aliases.lookup(form["definition"].get("country", "mozambique"),
                                    written_location)
        if alias_hits:
            for field, hit in alias_hits.items():
                written_location[field] = hit["canonical"]
            print(f"  location aliases applied: {', '.join(sorted(alias_hits))}")
        geo = location.resolve(
            district=written_location["district"],
            locality=written_location["locality"],
            place=written_location["village"],
        )
        result["location"] = geo.as_dict()
        if alias_hits:
            result["location"]["aliases_applied"] = {
                field: {"canonical": hit["canonical"], "level": hit["level"],
                        "pcode": hit["pcode"]}
                for field, hit in alias_hits.items()
            }
        (run_dir / "location.json").write_text(
            json.dumps(result["location"], indent=2, ensure_ascii=False)
        )
        print(f"  location: {location.summary(geo)}")

        status = "done" if not result["errors"] else "partial"
        run_hash = hashlib.sha256("".join(block_hashes).encode()).hexdigest()
        with connect() as conn:
            store.finish_run(
                conn,
                run_id,
                status=status,
                prompt_hash=run_hash,
                alignment_ms=alignment_ms,
                latency_ms=sum(b.get("latency_ms") or 0 for b in result["blocks"]),
                input_tokens=result["input_tokens"],
                output_tokens=result["output_tokens"],
                cost_usd=result["cost_usd"] or None,
                validation=validation,
                validation_status=validation["status"],
                location=result["location"],
                error="; ".join(result["errors"]) or None,
            )
            conn.commit()
        result["status"] = status
    except Exception as exc:
        with connect() as conn:
            store.finish_run(conn, run_id, status="failed", error=str(exc))
            conn.commit()
        result["status"] = "failed"
        result["errors"].append(str(exc))
        raise

    write_report(run_id, run_dir)
    result["accuracy"] = (
        round(result["correct"] / result["judged"], 4) if result["judged"] else None
    )
    (run_dir / "meta.json").write_text(json.dumps(result, indent=2, default=str))
    return result


def write_report(run_id, run_dir: Path) -> None:
    from sqlalchemy import text

    with connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT o.block_id, o.pair_id, o.concept_id, o.expected_value,
                       o.value, o.raw_value, o.correct
                FROM observations o
                WHERE o.run_id = :run_id
                ORDER BY o.block_id, o.pair_id
                """
            ),
            {"run_id": run_id},
        ).mappings()
        with (run_dir / "report.csv").open("w", newline="") as f:
            writer = csv.writer(f)
            writer.writerow(
                ["block", "pair", "concept", "expected", "value", "raw", "correct"]
            )
            for row in rows:
                writer.writerow(
                    [
                        row["block_id"],
                        row["pair_id"],
                        row["concept_id"],
                        row["expected_value"],
                        row["value"],
                        row["raw_value"],
                        row["correct"],
                    ]
                )
