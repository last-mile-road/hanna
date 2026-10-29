"""Delete card images older than the retention window, keep the ledger.

    python tools/cleanup_images.py --days 30 --dry-run
    python tools/cleanup_images.py --days 30

What goes: the uploaded original, the whole `output/runs/<id>/` directory for runs on that
card (aligned image, crops, validation.json, report.csv). What stays: `cards`,
`runs`, `block_runs`, `observations` and `ground_truth` rows, so accuracy and timing
history survives the images.

Shortcut: there is no `cards.deleted_at` column yet, so the card row keeps a path to a file
that no longer exists. Every deletion is appended to `output/cleanup.log` as JSON, which is
the audit trail until the column exists.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from sqlalchemy import text

from core import settings
from core.db import connect


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=settings.IMAGE_RETENTION_DAYS)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    log_path = settings.OUTPUT_DIR / "cleanup.log"
    removed: list[dict] = []

    with connect() as conn:
        cards = conn.execute(
            text(
                """
                SELECT c.id, c.image_path, c.created_at
                  FROM cards c
                 WHERE c.kind = 'upload'
                   AND c.created_at < now() - (:days || ' days')::interval
                 ORDER BY c.created_at
                """
            ),
            {"days": args.days},
        ).mappings().all()

        for card in cards:
            runs = conn.execute(
                text("SELECT id FROM runs WHERE card_id = :card_id"), {"card_id": card["id"]}
            ).scalars().all()

            image = Path(card["image_path"])
            targets = [image] if image.exists() else []
            targets += [
                settings.OUTPUT_DIR / "runs" / str(run_id)
                for run_id in runs
                if (settings.OUTPUT_DIR / "runs" / str(run_id)).exists()
            ]

            record = {
                "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                "card_id": card["id"],
                "card_created_at": card["created_at"].isoformat(),
                "runs": [str(r) for r in runs],
                "paths": [str(p) for p in targets],
                "dry_run": args.dry_run,
            }
            removed.append(record)
            if args.dry_run:
                continue
            for path in targets:
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)

        if not args.dry_run and removed:
            with log_path.open("a", encoding="utf-8") as fh:
                for record in removed:
                    fh.write(json.dumps(record) + "\n")
        conn.commit()

    total_paths = sum(len(r["paths"]) for r in removed)
    verb = "would remove" if args.dry_run else "removed"
    print(f"{verb} {total_paths} path(s) for {len(removed)} card(s) older than {args.days} days")
    for record in removed:
        print(f"  card {record['card_id']}: {len(record['paths'])} path(s)")
    if not args.dry_run and removed:
        print(f"audit trail: {log_path}")


if __name__ == "__main__":
    main()
