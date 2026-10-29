"""The job queue: the `jobs` table, claimed with SKIP LOCKED.

One worker loop, one statement per claim, no broker. `FOR UPDATE SKIP LOCKED` is the
primitive Postgres gives us (`DECISIONS.md` D-2026-10-26-01); a second worker can run
beside the first without taking the same job.

    worker_id = socket.gethostname()
    with connect() as conn:
        job = claim(conn, worker_id)
"""

from __future__ import annotations

import json
import uuid
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

CLAIM_SQL = text(
    """
    UPDATE jobs
       SET status = 'running',
           locked_by = :worker,
           locked_at = now(),
           attempts = attempts + 1
     WHERE id = (
        SELECT id FROM jobs
         WHERE status = 'queued'
           AND attempts < :max_attempts
         ORDER BY created_at
         FOR UPDATE SKIP LOCKED
         LIMIT 1
     )
    RETURNING id, kind, payload, attempts
    """
)

REQUEUE_STALE_SQL = text(
    """
    UPDATE jobs
       SET status = 'queued', locked_by = NULL, locked_at = NULL
     WHERE status = 'running'
       AND locked_at < now() - (:minutes || ' minutes')::interval
    RETURNING id, attempts
    """
)


def enqueue(conn: Connection, kind: str, payload: dict[str, Any]) -> uuid.UUID:
    job_id = uuid.uuid4()
    conn.execute(
        text(
            "INSERT INTO jobs (id, kind, payload, status) "
            "VALUES (:id, :kind, CAST(:payload AS jsonb), 'queued')"
        ),
        {"id": job_id, "kind": kind, "payload": json.dumps(payload)},
    )
    return job_id


def claim(conn: Connection, worker: str, max_attempts: int = 2) -> dict[str, Any] | None:
    row = conn.execute(
        CLAIM_SQL, {"worker": worker, "max_attempts": max_attempts}
    ).mappings().first()
    return dict(row) if row else None


def finish(conn: Connection, job_id: uuid.UUID, error: str | None = None) -> None:
    conn.execute(
        text(
            "UPDATE jobs SET status = :status, finished_at = now(), error = :error "
            "WHERE id = :id"
        ),
        {"id": job_id, "status": "failed" if error else "done", "error": error},
    )


def fail_retryable(conn: Connection, job_id: uuid.UUID, error: str, max_attempts: int = 2) -> str:
    """Requeue when attempts remain; otherwise park it as failed. Returns the new state."""
    attempts = conn.execute(
        text("SELECT attempts FROM jobs WHERE id = :id"), {"id": job_id}
    ).scalar_one()
    state = "queued" if attempts < max_attempts else "failed"
    conn.execute(
        text(
            "UPDATE jobs SET status = :status, error = :error, locked_by = NULL, "
            "locked_at = NULL, finished_at = CASE WHEN :status = 'failed' "
            "THEN now() ELSE NULL END WHERE id = :id"
        ),
        {"id": job_id, "status": state, "error": error},
    )
    return state


def requeue_stale(conn: Connection, minutes: int = 30) -> list[uuid.UUID]:
    """Recover jobs whose worker died mid-run. attempts already counted at claim."""
    rows = conn.execute(REQUEUE_STALE_SQL, {"minutes": minutes}).mappings().all()
    return [row["id"] for row in rows]
