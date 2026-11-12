"""Case, capture, page, visit and confirmed-value persistence.

The run ledger records one reading of one image. This module records the durable
case the next capture reads back: the HANNA number, the pages, the visits, which
sections a clinician has closed, and the confirmed values. Closed means closed:
the next capture shows the section as confirmed and does not read it again.

Shaped after a DHIS2 tracker (case = tracked entity, visit = event, concept =
data element) so the later sync is a mapping, not a migration.
"""

from __future__ import annotations

import uuid
from datetime import date
from pathlib import Path
from typing import Any

from sqlalchemy import case as sa_case
from sqlalchemy import func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.engine import Connection

from .caseid import generate_unique, is_valid, normalise
from .schema import (
    capture_pages,
    captures,
    case_values,
    cases,
    section_states,
    visits,
)


def resolve_case(conn: Connection, case_id: uuid.UUID) -> uuid.UUID:
    """Follow a merge pointer to the case that now holds the record."""
    seen = set()
    current = case_id
    for _ in range(8):
        if current in seen:
            break
        seen.add(current)
        target = conn.execute(
            select(cases.c.merged_into).where(cases.c.id == current)
        ).scalar()
        if target is None:
            break
        current = target
    return current


def merge_case(conn: Connection, from_id: uuid.UUID, into_id: uuid.UUID) -> None:
    """Join a provisional case to the case its number belongs to.

    The captures carry the pages and runs, so moving them is the whole merge.
    The provisional row keeps the pointer so a phone holding its id still
    resolves to the real case.
    """
    if from_id == into_id:
        return
    conn.execute(
        update(captures).where(captures.c.case_id == from_id).values(case_id=into_id)
    )
    conn.execute(
        update(cases)
        .where(cases.c.id == from_id)
        .values(merged_into=into_id, status="closed")
    )


def find_case(
    conn: Connection,
    *,
    case_id: uuid.UUID | None = None,
    country_id: int | None = None,
    hanna_number: str | None = None,
    government_id: str | None = None,
) -> dict[str, Any] | None:
    """Find one case by id, HANNA number, NIL, or country + NIL."""
    if not any((case_id, hanna_number, government_id)):
        raise ValueError("find_case needs a case id, a HANNA number or a government id")
    if case_id is not None:
        case_id = resolve_case(conn, case_id)
    query = select(cases)
    if case_id is not None:
        query = query.where(cases.c.id == case_id)
    if country_id is not None:
        query = query.where(cases.c.country_id == country_id)
    if hanna_number is not None:
        query = query.where(cases.c.hanna_number == normalise(hanna_number))
    if government_id is not None:
        query = query.where(cases.c.government_id == government_id)
    row = conn.execute(query.limit(1)).mappings().first()
    return dict(row) if row else None


def create_case(
    conn: Connection,
    country_id: int,
    *,
    hanna_number: str | None = None,
    government_id: str | None = None,
    identity_status: str = "new",
    status: str = "open",
    classification: str | None = None,
    treatment_start_date: date | None = None,
) -> dict[str, Any]:
    """Create a case. A new case is provisional and has no HANNA number yet.

    ``identity_status`` records how the capture reached this case:
    ``hanna_number`` (read from the card), ``nil`` (matched by the government
    number) or ``new`` (neither; a provisional case, deduplicated later).
    The number is allocated by :func:`allocate_number` only after extraction
    and validation have no blocking conditions.
    """
    if hanna_number is not None and not is_valid(hanna_number):
        raise ValueError(f"invalid HANNA number: {hanna_number!r}")
    case_id = uuid.uuid4()
    conn.execute(
        cases.insert().values(
            id=case_id,
            country_id=country_id,
            hanna_number=normalise(hanna_number) if hanna_number else None,
            government_id=government_id,
            identity_status=identity_status,
            status=status,
            classification=classification,
            treatment_start_date=treatment_start_date,
        )
    )
    return find_case(conn, case_id=case_id)  # type: ignore[return-value]


def allocate_number(conn: Connection, case_id: uuid.UUID) -> dict[str, Any]:
    """Allocate the HANNA number, once, after validation is clear.

    The caller is responsible for the rule: no blocking conditions, and for a
    closed card the outcome is filled. This is the proof that notification was
    completed, not a capture receipt.
    """
    case = find_case(conn, case_id=case_id)
    if case is None:
        raise ValueError("case not found")
    if case["hanna_number"]:
        return case
    number = generate_unique(conn, case["country_id"])
    result = conn.execute(
        update(cases)
        .where(cases.c.id == case_id, cases.c.hanna_number.is_(None))
        .values(hanna_number=number)
    )
    if result.rowcount == 0:  # another transaction won the race
        return find_case(conn, case_id=case_id)  # type: ignore[return-value]
    return find_case(conn, case_id=case_id)  # type: ignore[return-value]


def add_capture(
    conn: Connection,
    case_id: uuid.UUID,
    *,
    source: str = "camera",
    attested_dummy: bool = False,
    client: dict[str, Any] | None = None,
    status: str = "admitted",
) -> dict[str, Any]:
    capture_id = uuid.uuid4()
    conn.execute(
        captures.insert().values(
            id=capture_id,
            case_id=case_id,
            source=source,
            attested_dummy=attested_dummy,
            client=client,
            status=status,
        )
    )
    return dict(
        conn.execute(select(captures).where(captures.c.id == capture_id))
        .mappings()
        .one()
    )


def add_page(
    conn: Connection,
    capture_id: uuid.UUID,
    *,
    page_no: int,
    shot_no: int,
    image_path: str | Path,
    md5: str,
    variant: str | None = None,
    form_id: int | None = None,
    run_id: uuid.UUID | None = None,
    admission: str = "pending",
    quality: dict[str, Any] | None = None,
    match_inliers: int | None = None,
    match_margin: int | None = None,
    rotation: int | None = None,
) -> dict[str, Any]:
    return dict(
        conn.execute(
            capture_pages.insert()
            .values(
                capture_id=capture_id,
                page_no=page_no,
                shot_no=shot_no,
                image_path=str(image_path),
                md5=md5,
                variant=variant,
                form_id=form_id,
                run_id=run_id,
                admission=admission,
                quality=quality,
                match_inliers=match_inliers,
                match_margin=match_margin,
                rotation=rotation,
            )
            .returning(capture_pages)
        )
        .mappings()
        .one()
    )


def set_page_run(conn: Connection, page_id: int, run_id: uuid.UUID) -> None:
    """Link a page to its extraction run."""
    conn.execute(
        update(capture_pages)
        .where(capture_pages.c.id == page_id)
        .values(run_id=run_id)
    )


def upsert_visit(
    conn: Connection,
    case_id: uuid.UUID,
    *,
    visit_seq: int,
    visit_date: date | None = None,
    visit_label: str | None = None,
    date_status: str = "recorded",
    capture_id: uuid.UUID | None = None,
    source_page_id: int | None = None,
) -> dict[str, Any]:
    """Keyed by the printed column position, which survives a re-capture.

    A later photo with no written date does not erase a date already recorded.
    """
    values = {
        "case_id": case_id,
        "capture_id": capture_id,
        "source_page_id": source_page_id,
        "visit_seq": visit_seq,
        "visit_label": visit_label,
        "visit_date": visit_date,
        "date_status": date_status,
    }
    stmt = pg_insert(visits).values(**values)
    stmt = stmt.on_conflict_do_update(
        index_elements=[visits.c.case_id, visits.c.visit_seq],
        set_={
            "capture_id": stmt.excluded.capture_id,
            "source_page_id": stmt.excluded.source_page_id,
            "visit_label": stmt.excluded.visit_label,
            "visit_date": func.coalesce(stmt.excluded.visit_date, visits.c.visit_date),
            "date_status": sa_case(
                (stmt.excluded.visit_date.is_not(None), stmt.excluded.date_status),
                else_=visits.c.date_status,
            ),
        },
    )
    conn.execute(stmt)
    return dict(
        conn.execute(
            select(visits).where(
                visits.c.case_id == case_id,
                visits.c.visit_seq == visit_seq,
            )
        )
        .mappings()
        .one()
    )


def set_section_state(
    conn: Connection,
    case_id: uuid.UUID,
    section_id: str,
    *,
    state: str = "closed",
    visit_id: int | None = None,
    capture_id: uuid.UUID | None = None,
    confirmed_by: str | None = None,
    reopened_by: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    """Close or re-open a section. Closing is what the next capture skips."""
    if state not in {"open", "closed"}:
        raise ValueError("state must be 'open' or 'closed'")
    values = {
        "case_id": case_id,
        "visit_id": visit_id,
        "section_id": section_id,
        "state": state,
        "capture_id": capture_id,
        "confirmed_by": confirmed_by,
        "confirmed_at": func.now() if state == "closed" else None,
        "reopened_by": reopened_by,
        "reopened_at": func.now() if state == "open" else None,
        "note": note,
    }
    keys = ("case_id", "section_id") if visit_id is None else (
        "case_id", "visit_id", "section_id"
    )
    update = {k: v for k, v in values.items() if k not in keys}
    if state == "open":
        # Re-opening must not erase who confirmed the section and when: that is
        # the audit line. The current state is open; the old confirmation stays.
        update["confirmed_by"] = section_states.c.confirmed_by
        update["confirmed_at"] = section_states.c.confirmed_at
    stmt = pg_insert(section_states).values(**values)
    if visit_id is None:
        stmt = stmt.on_conflict_do_update(
            index_elements=[section_states.c.case_id, section_states.c.section_id],
            index_where=section_states.c.visit_id.is_(None),
            set_=update,
        )
    else:
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                section_states.c.case_id,
                section_states.c.visit_id,
                section_states.c.section_id,
            ],
            index_where=section_states.c.visit_id.is_not(None),
            set_=update,
        )
    conn.execute(stmt)
    query = select(section_states).where(
        section_states.c.case_id == case_id,
        section_states.c.section_id == section_id,
    )
    query = (
        query.where(section_states.c.visit_id.is_(None))
        if visit_id is None
        else query.where(section_states.c.visit_id == visit_id)
    )
    return dict(conn.execute(query).mappings().one())


def put_case_value(
    conn: Connection,
    case_id: uuid.UUID,
    concept_id: str,
    value: str | None,
    *,
    visit_id: int | None = None,
    source_capture_id: uuid.UUID | None = None,
    source_page_id: int | None = None,
    confirmed_by: str | None = None,
) -> None:
    """Write one confirmed value. ``value=None`` means confirmed blank."""
    values = {
        "case_id": case_id,
        "visit_id": visit_id,
        "concept_id": concept_id,
        "value": value,
        "source_capture_id": source_capture_id,
        "source_page_id": source_page_id,
        "confirmed_by": confirmed_by,
        "confirmed_at": func.now(),
    }
    stmt = pg_insert(case_values).values(**values)
    if visit_id is None:
        stmt = stmt.on_conflict_do_update(
            index_elements=[case_values.c.case_id, case_values.c.concept_id],
            index_where=case_values.c.visit_id.is_(None),
            set_={k: v for k, v in values.items() if k not in ("case_id", "concept_id")},
        )
    else:
        stmt = stmt.on_conflict_do_update(
            index_elements=[
                case_values.c.case_id,
                case_values.c.visit_id,
                case_values.c.concept_id,
            ],
            index_where=case_values.c.visit_id.is_not(None),
            set_={
                k: v
                for k, v in values.items()
                if k not in ("case_id", "visit_id", "concept_id")
            },
        )
    conn.execute(stmt)


def confirmed_values(
    conn: Connection, case_id: uuid.UUID, *, visit_id: int | None = None
) -> dict[str, str | None]:
    """The confirmed projection: what the next capture reads instead of re-reading."""
    query = select(case_values.c.concept_id, case_values.c.value).where(
        case_values.c.case_id == case_id
    )
    query = (
        query.where(case_values.c.visit_id.is_(None))
        if visit_id is None
        else query.where(case_values.c.visit_id == visit_id)
    )
    return {row["concept_id"]: row["value"] for row in conn.execute(query).mappings()}


def closed_sections(
    conn: Connection, case_id: uuid.UUID, *, visit_id: int | None = None
) -> set[tuple[int | None, str]]:
    """The sections the next capture must show as confirmed and not read."""
    query = select(section_states.c.visit_id, section_states.c.section_id).where(
        section_states.c.case_id == case_id,
        section_states.c.state == "closed",
    )
    if visit_id is not None:
        query = query.where(section_states.c.visit_id == visit_id)
    return {(row["visit_id"], row["section_id"]) for row in conn.execute(query).mappings()}
