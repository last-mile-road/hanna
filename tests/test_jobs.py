"""Queue behaviour on the real dev database.

Skipped when Postgres is not running: the suite stays runnable without a database.
"""

from __future__ import annotations

import pytest
from sqlalchemy import text

from core import jobs
from core.db import connect


def _db_available() -> bool:
    try:
        with connect() as conn:
            conn.execute(text("select 1"))
        return True
    except Exception:
        return False


pytestmark = pytest.mark.skipif(not _db_available(), reason="dev database not running")


def _clean_test_jobs() -> None:
    with connect() as conn:
        conn.execute(text("delete from jobs where kind = 'test'"))
        conn.commit()


def test_enqueue_and_claim_are_exclusive():
    _clean_test_jobs()
    with connect() as conn:
        job_id = jobs.enqueue(conn, "test", {"x": 1})
        conn.commit()

    with connect() as first, connect() as second:
        claimed = jobs.claim(first, "worker-a")
        assert claimed is not None, "the queued job should be claimable"
        assert str(claimed["id"]) == str(job_id)
        blocked = jobs.claim(second, "worker-b")
        assert blocked is None, "SKIP LOCKED must not hand the same job to two workers"
        jobs.finish(first, job_id)
        first.commit()
        second.rollback()

    with connect() as conn:
        conn.execute(text("delete from jobs where id = :id"), {"id": job_id})
        conn.commit()


def test_retry_parks_after_max_attempts():
    _clean_test_jobs()
    with connect() as conn:
        job_id = jobs.enqueue(conn, "test", {})
        conn.execute(text("update jobs set attempts = 2 where id = :id"), {"id": job_id})
        conn.commit()
        conn.execute(text("update jobs set status = 'running' where id = :id"), {"id": job_id})
        state = jobs.fail_retryable(conn, job_id, "boom", max_attempts=2)
        conn.commit()
        assert state == "failed"

        conn.execute(
            text("update jobs set status = 'running', attempts = 1 where id = :id"),
            {"id": job_id},
        )
        assert jobs.fail_retryable(conn, job_id, "boom", max_attempts=2) == "queued"
        conn.commit()
        conn.execute(text("delete from jobs where id = :id"), {"id": job_id})
        conn.commit()


def test_stale_lock_is_requeued():
    _clean_test_jobs()
    with connect() as conn:
        job_id = jobs.enqueue(conn, "test", {})
        conn.execute(
            text(
                "update jobs set status = 'running', locked_by = 'dead', "
                "locked_at = now() - interval '2 hours' where id = :id"
            ),
            {"id": job_id},
        )
        conn.commit()
        recovered = jobs.requeue_stale(conn, minutes=30)
        conn.commit()
        assert job_id in recovered
        status = conn.execute(
            text("select status from jobs where id = :id"), {"id": job_id}
        ).scalar_one()
        assert status == "queued"
        conn.execute(text("delete from jobs where id = :id"), {"id": job_id})
        conn.commit()
