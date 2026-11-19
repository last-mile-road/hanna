"""Load and validate a country form pack against the concept index."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from . import settings

BLOCK_TYPES = {
    "value_pairs",
    "tick_grid",
    "annotated_table",
    "visual_annotation",
    "dose_grid",
    "free_text",
    "sketch",
}


@dataclass
class Pair:
    id: str
    block_id: str
    concept: str
    value_type: str
    bbox: list[int] | None
    options: list[Any] | None
    sensitivity: str
    label_local: str | None
    derived: bool
    ordinal: int


@dataclass
class Block:
    id: str
    block_type: str
    bbox: list[int] | None
    ordinal: int
    hint: str | None = None
    pairs: list[Pair] = field(default_factory=list)


@dataclass
class FormPack:
    form: dict[str, Any]
    blocks: list[Block]
    pairs: list[Pair]
    concepts: dict[str, dict[str, Any]]
    path: Path

    def concept(self, concept_id: str) -> dict[str, Any]:
        return self.concepts[concept_id]


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open() as f:
        return yaml.safe_load(f)


def load_concepts(path: Path | None = None) -> dict[str, dict[str, Any]]:
    data = _read_yaml(path or settings.CONCEPTS_PATH)
    return {c["id"]: c for c in data["concepts"]}


def country_of(definition: dict) -> str:
    """The country a form pack declares. No default: a pack that does not name
    its country is a broken pack, not a Mozambican one."""
    country = (definition or {}).get("country")
    if not country:
        raise ValueError("form pack does not declare a country")
    return str(country)


def load_form_pack(
    country: str,
    form_file: str = "form.draft.yaml",
    concepts_path: Path | None = None,
) -> FormPack:
    path = settings.COUNTRIES_DIR / country / form_file
    data = _read_yaml(path)
    concepts = load_concepts(concepts_path)

    blocks: list[Block] = []
    pairs: list[Pair] = []
    for b_ord, raw_block in enumerate(data["blocks"]):
        block = Block(
            id=raw_block["id"],
            block_type=raw_block["block_type"],
            bbox=raw_block.get("bbox"),
            ordinal=b_ord,
            hint=raw_block.get("hint"),
        )
        for p_ord, raw_pair in enumerate(raw_block.get("value_pairs", [])):
            concept = concepts.get(raw_pair.get("concept", ""), {})
            options = raw_pair.get("allowed_values")
            if options is None:
                options = concept.get("observed") or concept.get("options")
            pair = Pair(
                id=raw_pair["id"],
                block_id=block.id,
                concept=raw_pair.get("concept", ""),
                value_type=raw_pair.get("value_type") or concept.get("value_type", "text"),
                bbox=raw_pair.get("bbox"),
                options=options,
                sensitivity=raw_pair.get("sensitivity")
                or concept.get("sensitivity", "clinical"),
                label_local=raw_pair.get("label_local"),
                derived=bool(raw_pair.get("derived", False)),
                ordinal=p_ord,
            )
            block.pairs.append(pair)
            pairs.append(pair)
        blocks.append(block)

    labels_path = path.parent / "labels.yaml"
    if labels_path.exists():
        overlay = yaml.safe_load(labels_path.read_text()) or {}
        labels = overlay.get("labels", {})
        for pair in pairs:
            pair.label_local = labels.get(pair.id, pair.label_local)
        block_bbox = overlay.get("block_bbox", {})
        for block in blocks:
            if block.id in block_bbox:
                block.bbox = block_bbox[block.id]
        block_hints = overlay.get("block_hints", {})
        for block in blocks:
            if block.id in block_hints:
                block.hint = block_hints[block.id]

    pack = FormPack(form=data["form"], blocks=blocks, pairs=pairs, concepts=concepts, path=path)
    errors = validate(pack)
    if errors:
        raise ValueError("form pack is invalid:\n- " + "\n- ".join(errors))
    return pack


def _valid_bbox(bbox: Any) -> bool:
    return (
        isinstance(bbox, list)
        and len(bbox) == 4
        and all(isinstance(v, int) for v in bbox)
        and bbox[0] < bbox[2]
        and bbox[1] < bbox[3]
    )


def validate(pack: FormPack) -> list[str]:
    errors: list[str] = []
    form = pack.form
    for key in ("id", "country", "template"):
        if not form.get(key):
            errors.append(f"form.{key} is required")
    template = settings.COUNTRIES_DIR / form.get("country", "") / str(form.get("template", ""))
    if form.get("country") and form.get("template") and not template.exists():
        errors.append(f"template not found: {template}")

    block_ids: set[str] = set()
    pair_ids: set[str] = set()
    for block in pack.blocks:
        if block.id in block_ids:
            errors.append(f"duplicate block id: {block.id}")
        block_ids.add(block.id)
        if block.block_type not in BLOCK_TYPES:
            errors.append(f"block {block.id}: unknown block_type {block.block_type!r}")
        if block.bbox is not None and not _valid_bbox(block.bbox):
            errors.append(f"block {block.id}: bbox must be four increasing ints")
        for pair in block.pairs:
            if pair.id in pair_ids:
                errors.append(f"duplicate pair id: {pair.id}")
            pair_ids.add(pair.id)
            if pair.concept not in pack.concepts:
                errors.append(f"pair {pair.id}: concept {pair.concept!r} is not in the index")
            if pair.bbox is not None and not _valid_bbox(pair.bbox):
                errors.append(f"pair {pair.id}: bbox must be four increasing ints")
            if pair.bbox and block.bbox:
                if not (
                    block.bbox[0] <= pair.bbox[0]
                    and block.bbox[1] <= pair.bbox[1]
                    and pair.bbox[2] <= block.bbox[2]
                    and pair.bbox[3] <= block.bbox[3]
                ):
                    errors.append(f"pair {pair.id}: bbox lies outside block {block.id}")

    counts: dict[str, int] = {}
    for pair in pack.pairs:
        counts[pair.concept] = counts.get(pair.concept, 0) + 1
    for concept_id, n in counts.items():
        if n > 1 and pack.concepts.get(concept_id, {}).get("cardinality") != "many":
            errors.append(
                f"concept {concept_id} is used by {n} pairs but is not declared cardinality: many"
            )
    return errors


def prompt_options(pair: Pair, concept: dict[str, Any]) -> list[Any] | None:
    """Vocabulary the card actually uses, falling back to the canonical options."""
    return concept.get("observed") or pair.options or concept.get("options")
