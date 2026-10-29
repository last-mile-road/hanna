"""The worker. One loop, one job at a time per process.

    python -m server.worker                 # forever
    python -m server.worker --once          # one claim, for cron or tests

Concurrency is Postgres `FOR UPDATE SKIP LOCKED` (core/jobs.py). Every block is written to
the ledger as it completes, so the SSE endpoint can read progress without an in-process
bus, and a worker restart loses at most the block in flight.
"""

from __future__ import annotations

import argparse
import json
import os
import socket
import time
import uuid

from core import jobs, store
from core.db import connect
from core.run import run_card

WORKER_ID = f"{socket.gethostname()}-{os.getpid()}"


def run_job(job: dict) -> None:
    payload = job["payload"] or {}
    run_id = uuid.UUID(payload["run_id"])
    try:
        run_card(
            card_id=payload["card_id"],
            run_id=run_id,
            routing=bool(payload.get("routing", True)),
            purpose=payload.get("purpose", "upload"),
        )
    except Exception as exc:  # the run row is marked failed; the job records why
        with connect() as conn:
            store.finish_run(conn, run_id, status="failed", error=str(exc)[:2000])
            jobs.finish(conn, job["id"], error=f"{type(exc).__name__}: {exc}"[:2000])
            conn.commit()
        raise
    with connect() as conn:
        jobs.finish(conn, job["id"])
        conn.commit()


def work_once() -> bool:
    """Claim and run one job. Returns False when the queue is empty."""
    with connect() as conn:
        job = jobs.claim(conn, WORKER_ID)
        conn.commit()
    if job is None:
        return False
    try:
        run_job(job)
    except Exception as exc:
        print(f"job {job['id']} failed: {exc}", flush=True)
        with connect() as conn:
            state = jobs.fail_retryable(conn, job["id"], str(exc)[:2000])
            conn.commit()
        print(f"job {job['id']} -> {state}", flush=True)
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--once", action="store_true", help="run one job and exit")
    ap.add_argument("--poll", type=float, default=0.5, help="idle sleep in seconds")
    ap.add_argument("--stale-minutes", type=int, default=30,
                    help="requeue jobs locked longer than this (dead worker)")
    args = ap.parse_args()

    with connect() as conn:
        recovered = jobs.requeue_stale(conn, args.stale_minutes)
        conn.commit()
    if recovered:
        print(f"requeued {len(recovered)} stale job(s): {recovered}", flush=True)

    if args.once:
        print(json.dumps({"worked": work_once()}))
        return
    print(f"worker {WORKER_ID} waiting for jobs", flush=True)
    while True:
        if not work_once():
            time.sleep(args.poll)


if __name__ == "__main__":
    main()
