"""How far reviewers move the pin: the moved_m distribution.

One review gave `moved_m 34.6`. One number is not a distribution. This report
reads every run's `location.review` and answers the question behind the next
design decision: is the gazetteer good enough (reviewers confirm or nudge the
pin inside its precision ring), or does tier 2 earn its place (reviewers
repeatedly move pins far outside the ring)?

The measurement is designed to accumulate: run it after every review round and
compare against the precision of the level the gazetteer claimed.

    facility      250 m      locality   1.5 km
    admin post    4.0 km     district   12 km

Interpretation guide:
  - moved_m under the claimed precision -> the gazetteer answer was usable;
  - moved_m beyond it, on the same level, again and again -> tier 2 matters;
  - mode "accepted" always counts as moved_m 0 for the distribution, but is
    reported separately: a confirm is the strongest vote of confidence.

Usage:
    ~/.venv/bin/python tools/moved_m_report.py [--country mozambique]
"""

from __future__ import annotations

import argparse
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.db import connect  # noqa: E402
from core.location import PRECISION_METRES, _km  # noqa: E402
from sqlalchemy import text  # noqa: E402

# The precision ring is the promise the review map makes. A reviewer moving the
# pin less than this far is correcting at a finer grain than the data has.
PRECISION_BANDS = [("ring", 1.0), ("2x ring", 2.0), ("5x ring", 5.0)]


def rows(country: str | None):
    with connect() as conn:
        query = text("SELECT id, started_at, location FROM runs ORDER BY started_at")
        for run_id, started_at, location in conn.execute(query):
            if not location:
                continue
            if country and (location.get("country") or "") != country:
                pass  # location records do not carry country yet; filter is a no-op
            review = location.get("review")
            if not review:
                continue
            yield run_id, started_at, location, review


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--country", default="")
    args = ap.parse_args()

    entries = []
    for run_id, started_at, location, review in rows(args.country or None):
        level = (location.get("chosen") or {}).get("level") or location.get("level")
        precision = PRECISION_METRES.get(level or "")
        moved = review.get("moved_m")
        entries.append({
            "run_id": run_id,
            "started_at": str(started_at)[:19],
            "level": level or "?",
            "precision_m": precision,
            "mode": review.get("mode"),
            "changed": review.get("changed"),
            "moved_m": float(moved) if moved is not None else None,
            "lat": review.get("lat"),
            "lon": review.get("lon"),
        })

    if not entries:
        print("no reviewed location runs yet — the distribution starts with the first review")
        return

    print(f"reviewed location runs: {len(entries)}\n")
    print(f"{'run started':20s} {'decision level':18s} {'precision':>9s} {'mode':10s} {'moved_m':>9s}")
    for e in entries:
        precision = f"{e['precision_m']:,.0f} m" if e["precision_m"] else "-"
        moved = f"{e['moved_m']:.1f}" if e["moved_m"] is not None else "-"
        print(f"{e['started_at']:20s} {e['level']:18s} {precision:>9s} {str(e['mode']):10s} {moved:>9s}")

    moved_values = [e["moved_m"] for e in entries if e["moved_m"] is not None]
    confirms = [e for e in entries if e["mode"] == "accepted"]
    moves = [e for e in entries if e["mode"] != "accepted" and e["moved_m"] is not None]

    print()
    print(f"confirms without moving (mode=accepted): {len(confirms)}/{len(entries)}")
    if moved_values:
        mean = statistics.mean(moved_values)
        median = statistics.median(moved_values)
        print(f"moved_m: n={len(moved_values)} mean={mean:.1f} median={median:.1f} "
              f"max={max(moved_values):.1f}")
        # share of pins inside the precision the gazetteer claimed
        banded = [e for e in entries if e["moved_m"] is not None and e["precision_m"]]
        for label, factor in PRECISION_BANDS:
            inside = [e for e in banded if e["moved_m"] <= factor * e["precision_m"]]
            share = 100.0 * len(inside) / len(banded) if banded else 0.0
            print(f"inside {label} of claimed precision: {len(inside)}/{len(banded)} ({share:.0f}%)")

    if len(entries) < 5:
        print(f"\nnote: n={len(entries)} is not a distribution. The decision — gazetteer "
              "good enough or tier 2 earns its place — needs a round of reviews across "
              "reviewers and card types before it is made.")


if __name__ == "__main__":
    main()
