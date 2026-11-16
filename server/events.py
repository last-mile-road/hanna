"""Read models for the API and the SSE stream.

The ledger is the event source. `block_runs` rows appear as each block completes, so a
poller needs no broker and survives a worker restart: the stream reads what the database
already recorded (`DECISIONS.md` D-2026-10-28-10: streaming is progress, not permission).
"""

from __future__ import annotations

from typing import Any, Iterable

from sqlalchemy import text
from sqlalchemy.engine import Connection


def run_row(conn: Connection, run_id: str) -> dict[str, Any] | None:
    row = conn.execute(
        text(
            """
            SELECT id, card_id, form_id, form_version, profile_id, status, purpose,
                   started_at, finished_at, alignment_ms, latency_ms,
                   input_tokens, output_tokens, cost_usd,
                   validation, validation_status, location, error
              FROM runs WHERE id = CAST(:run_id AS uuid)
            """
        ),
        {"run_id": run_id},
    ).mappings().first()
    return dict(row) if row else None


def block_rows(conn: Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT id, block_id, block_type, profile_id, status, error,
                   latency_ms, input_tokens, output_tokens
              FROM block_runs WHERE run_id = CAST(:run_id AS uuid)
             ORDER BY id
            """
        ),
        {"run_id": run_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def observation_rows(conn: Connection, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        text(
            """
            SELECT block_id, pair_id, concept_id, value, raw_value, expected_value, correct
              FROM observations WHERE run_id = CAST(:run_id AS uuid)
             ORDER BY block_id, pair_id
            """
        ),
        {"run_id": run_id},
    ).mappings().all()
    return [dict(row) for row in rows]


def review_progress(conn: Connection, run_id: str) -> dict[str, Any]:
    """How much of this run a human has decided: accepted, corrected or skipped.

    Only pairs the review screen shows count. Location fields, the HANNA number
    and the dev-only fields (the NIL, the internal notes) never appear there, so
    requiring a decision on them would leave the run permanently unfinished.
    """
    from core import uischema

    row = conn.execute(
        text(
            "SELECT r.form_id, f.definition "
            "  FROM runs r JOIN forms f ON f.id = r.form_id "
            " WHERE r.id = CAST(:run_id AS uuid)"
        ),
        {"run_id": run_id},
    ).mappings().first()
    if row is None:
        return {"reviewed": 0, "total": 0, "finished": False}
    layout = uischema.layout(row["definition"])
    # Fields the screen never shows: the location block (confirmed on a map) and
    # the developer-only detail. A reviewer cannot decide them, so they cannot
    # hold the run open.
    skipped = list(layout.get("dev_only") or []) + list(layout.get("hidden") or [])

    counts = conn.execute(
        text(
            "SELECT count(*) AS total, "
            "       count(*) FILTER (WHERE o.review_mode IS NOT NULL) AS reviewed "
            "  FROM observations o "
            "  JOIN form_pairs fp ON fp.form_id = :form_id AND fp.pair_id = o.pair_id "
            " WHERE o.run_id = CAST(:run_id AS uuid) "
            "   AND NOT (o.pair_id = ANY(:dev_only))"
        ),
        {"run_id": run_id, "form_id": row["form_id"], "dev_only": skipped},
    ).mappings().one()
    total = int(counts["total"] or 0)
    reviewed = int(counts["reviewed"] or 0)
    finished_at = conn.execute(
        text("SELECT review_finished_at FROM runs WHERE id = CAST(:run_id AS uuid)"),
        {"run_id": run_id},
    ).scalar()
    return {
        "reviewed": reviewed,
        "total": total,
        "finished": finished_at is not None,
        "finished_at": finished_at.isoformat() if finished_at else None,
    }


def snapshot(conn: Connection, run_id: str) -> dict[str, Any] | None:
    run = run_row(conn, run_id)
    if run is None:
        return None
    blocks = block_rows(conn, run_id)
    values: dict[str, dict[str, Any]] = {}
    for obs in observation_rows(conn, run_id):
        values.setdefault(obs["block_id"], {})[obs["pair_id"]] = obs["value"]
    for block in blocks:
        block["values"] = values.get(block["block_id"], {})
        block.pop("id", None)
    return {
        "run_id": str(run["id"]),
        "status": run["status"],
        "profile_id": run["profile_id"],
        "form_version": run["form_version"],
        "started_at": run["started_at"].isoformat() if run["started_at"] else None,
        "finished_at": run["finished_at"].isoformat() if run["finished_at"] else None,
        "alignment_ms": run["alignment_ms"],
        "latency_ms": run["latency_ms"],
        "cost_usd": float(run["cost_usd"]) if run["cost_usd"] is not None else None,
        "validation_status": run["validation_status"],
        "validation": run["validation"],
        "error": run["error"],
        "blocks": blocks,
        "blocks_done": len(blocks),
        "review": review_progress(conn, run_id),
    }


def new_blocks(blocks: Iterable[dict[str, Any]], seen: set[str]) -> list[dict[str, Any]]:
    """Pure helper for the stream: blocks not yet sent, and the ids to remember."""
    fresh = [block for block in blocks if block["block_id"] not in seen]
    seen.update(block["block_id"] for block in fresh)
    return fresh
