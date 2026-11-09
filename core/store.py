"""Persistence helpers for the form pack, ground truth and the run ledger."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path
from typing import Any

from sqlalchemy import delete, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from .forms import Block, FormPack, Pair
from .schema import (
    block_runs,
    cards,
    concepts as concepts_t,
    countries,
    form_blocks,
    form_pairs,
    forms,
    ground_truth,
    observations,
    profiles,
    runs,
)


def md5(path: Path) -> str:
    digest = hashlib.md5()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def upsert_country(conn: Connection, code: str, name: str) -> int:
    stmt = pg_insert(countries).values(code=code, name=name)
    stmt = stmt.on_conflict_do_update(index_elements=[countries.c.code], set_={"name": name})
    conn.execute(stmt)
    return conn.execute(select(countries.c.id).where(countries.c.code == code)).scalar_one()


def upsert_concepts(conn: Connection, concepts: dict[str, dict[str, Any]]) -> None:
    for concept in concepts.values():
        values = {
            "id": concept["id"],
            "who_name": concept.get("who_name"),
            "value_type": concept.get("value_type", "text"),
            "options": concept.get("options"),
            "observed": concept.get("observed"),
            "aliases": concept.get("aliases"),
            "value_aliases": concept.get("value_aliases"),
            "sensitivity": concept.get("sensitivity", "clinical"),
            "tier": concept.get("tier", "local"),
            "card_marks": concept.get("card_marks"),
            "unit": concept.get("unit"),
            "min_value": concept.get("min"),
            "sanity_max": concept.get("sanity_max"),
            "note": concept.get("note"),
        }
        stmt = pg_insert(concepts_t).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[concepts_t.c.id],
            set_={k: v for k, v in values.items() if k != "id"},
        )
        conn.execute(stmt)


def upsert_form(
    conn: Connection,
    country_id: int,
    pack: FormPack,
    version: int = 1,
    status: str = "draft",
) -> int:
    form = pack.form
    template_path = Path(profile_template_path(pack))
    values = {
        "country_id": country_id,
        "form_key": form["id"],
        "version": version,
        "status": status,
        "definition": form,
        "template_path": str(template_path),
        "template_md5": md5(template_path),
        "reference_year": form.get("reference_year"),
    }
    stmt = pg_insert(forms).values(**values)
    stmt = stmt.on_conflict_do_update(
        constraint="uq_forms_key_version",
        set_={k: v for k, v in values.items() if k not in {"country_id", "form_key", "version"}},
    ).returning(forms.c.id)
    form_id = conn.execute(stmt).scalar_one()

    conn.execute(delete(form_pairs).where(form_pairs.c.form_id == form_id))
    conn.execute(delete(form_blocks).where(form_blocks.c.form_id == form_id))
    conn.execute(
        form_blocks.insert(),
        [
            {
                "form_id": form_id,
                "block_id": block.id,
                "block_type": block.block_type,
                "bbox": block.bbox,
                "hint": block.hint,
                "ordinal": block.ordinal,
            }
            for block in pack.blocks
        ],
    )
    conn.execute(
        form_pairs.insert(),
        [
            {
                "form_id": form_id,
                "pair_id": pair.id,
                "block_id": pair.block_id,
                "concept_id": pair.concept,
                "value_type": pair.value_type,
                "bbox": pair.bbox,
                "options": pair.options,
                "sensitivity": pair.sensitivity,
                "label_local": pair.label_local,
                "derived": pair.derived,
                "ordinal": pair.ordinal,
            }
            for pair in pack.pairs
        ],
    )
    return form_id


def profile_template_path(pack: FormPack) -> str:
    return str(pack.path.parent / str(pack.form["template"]))


def upsert_profile(conn: Connection, profile: dict[str, Any]) -> None:
    stmt = pg_insert(profiles).values(**profile)
    stmt = stmt.on_conflict_do_update(
        index_elements=[profiles.c.id],
        set_={k: v for k, v in profile.items() if k != "id"},
    )
    conn.execute(stmt)


def upsert_card(
    conn: Connection,
    country_id: int,
    image_path: Path,
    kind: str = "dummy",
    attested_dummy: bool = True,
) -> int:
    import cv2

    image = cv2.imread(str(image_path))
    if image is None:
        raise FileNotFoundError(f"card image not found: {image_path}")
    height, width = image.shape[:2]
    checksum = md5(image_path)
    values = {
        "country_id": country_id,
        "kind": kind,
        "image_path": str(image_path),
        "md5": checksum,
        "width": width,
        "height": height,
        "attested_dummy": attested_dummy,
    }
    stmt = pg_insert(cards).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[cards.c.md5],
        set_={k: v for k, v in values.items() if k not in {"country_id", "md5"}},
    ).returning(cards.c.id)
    return conn.execute(stmt).scalar_one()


def replace_ground_truth(
    conn: Connection, card_id: int, rows: list[dict[str, Any]], version: int = 1
) -> None:
    conn.execute(
        delete(ground_truth).where(
            ground_truth.c.card_id == card_id, ground_truth.c.version == version
        )
    )
    if rows:
        conn.execute(
            ground_truth.insert(),
            [
                {
                    "card_id": card_id,
                    "gt_key": row["gt_key"],
                    "concept_id": row["concept_id"],
                    "pair_id": row.get("pair_id"),
                    "value": row["value"],
                    "status": row.get("status", "verified"),
                    "scoreable": row.get("scoreable", True),
                    "source": row.get("source"),
                    "verified_by": row.get("verified_by"),
                    "verified_at": row.get("verified_at"),
                    "version": version,
                }
                for row in rows
            ],
        )


def upsert_ground_truth(
    conn: Connection, card_id: int, rows: list[dict[str, Any]], version: int = 1
) -> None:
    """Write reviewed values as the card's ground truth, one row per pair."""
    for row in rows:
        values = {"card_id": card_id, "version": version, **row}
        stmt = pg_insert(ground_truth).values(**values)
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                ground_truth.c.card_id,
                ground_truth.c.gt_key,
                ground_truth.c.version,
            ],
            set_={
                key: value
                for key, value in values.items()
                if key not in ("card_id", "gt_key", "version")
            },
        )
        conn.execute(stmt)


def ground_truth_map(conn: Connection, card_id: int, version: int = 1) -> dict[str, dict[str, Any]]:
    rows = conn.execute(
        select(ground_truth).where(
            ground_truth.c.card_id == card_id,
            ground_truth.c.version == version,
            ground_truth.c.status == "verified",
            ground_truth.c.scoreable.is_(True),
            ground_truth.c.pair_id.is_not(None),
        )
    ).mappings()
    return {row["pair_id"]: dict(row) for row in rows}


def form_rows(conn: Connection, form_id: int) -> tuple[list[Block], list[Pair], dict[str, dict[str, Any]]]:
    block_rows = conn.execute(
        select(form_blocks).where(form_blocks.c.form_id == form_id).order_by(form_blocks.c.ordinal)
    ).mappings()
    pair_rows = conn.execute(
        select(form_pairs).where(form_pairs.c.form_id == form_id).order_by(form_pairs.c.ordinal)
    ).mappings()
    concept_rows = conn.execute(select(concepts_t)).mappings()

    concepts = {row["id"]: dict(row) for row in concept_rows}
    blocks = [
        Block(
            id=row["block_id"],
            block_type=row["block_type"],
            bbox=row["bbox"],
            ordinal=row["ordinal"],
            hint=row["hint"],
        )
        for row in block_rows
    ]
    pairs = [
        Pair(
            id=row["pair_id"],
            block_id=row["block_id"],
            concept=row["concept_id"],
            value_type=row["value_type"],
            bbox=row["bbox"],
            options=row["options"],
            sensitivity=row["sensitivity"],
            label_local=row["label_local"],
            derived=row["derived"],
            ordinal=row["ordinal"],
        )
        for row in pair_rows
    ]
    by_block: dict[str, list[Pair]] = {}
    for pair in pairs:
        by_block.setdefault(pair.block_id, []).append(pair)
    for block in blocks:
        block.pairs = by_block.get(block.id, [])
    return blocks, pairs, concepts


def create_run(
    conn: Connection,
    card_id: int,
    form_id: int,
    form_version: int,
    profile_id: str,
    purpose: str,
    status: str = "running",
) -> uuid.UUID:
    run_id = uuid.uuid4()
    conn.execute(
        runs.insert().values(
            id=run_id,
            card_id=card_id,
            form_id=form_id,
            form_version=form_version,
            profile_id=profile_id,
            status=status,
            purpose=purpose,
        )
    )
    return run_id


def finish_run(conn: Connection, run_id: uuid.UUID, **values: Any) -> None:
    from sqlalchemy import func

    values.setdefault("finished_at", func.now())
    conn.execute(update(runs).where(runs.c.id == run_id).values(**values))


def add_block_run(conn: Connection, values: dict[str, Any]) -> int:
    return conn.execute(block_runs.insert().values(**values).returning(block_runs.c.id)).scalar_one()


def add_observations(conn: Connection, rows: list[dict[str, Any]]) -> None:
    if rows:
        conn.execute(observations.insert(), rows)
