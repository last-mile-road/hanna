"""Fuzzy match a written place name against the Mozambique gazetteer.

Read-only experiment. The gazetteer is the v5 Cabo Delgado admin database:
provinces -> districts -> administrative posts. The card's "Localidade" sits at
the administrative-post level; villages are not in that database.

Usage:
  python tools/location_fuzzy.py --value Chiave --level district
  python tools/location_fuzzy.py --value Namogotria --level locality --district Chiure
"""

from __future__ import annotations

import argparse
import sqlite3
import sys
import unicodedata
from pathlib import Path

from rapidfuzz import fuzz, process

DEFAULT_DB = (
    Path(__file__).resolve().parents[2]
    / "hanna_archive/versions/v4_hanna3_2025-2026/backups/hanna_scalable_Nov_16/cabo_delgado_admin.db"
)


def key(value: str) -> str:
    text = unicodedata.normalize("NFKD", str(value))
    text = "".join(c for c in text if not unicodedata.combining(c))
    return " ".join(text.lower().split())


def rank(value: str, candidates: list[str], limit: int = 3):
    scored = [
        (candidate, fuzz.ratio(key(value), key(candidate)))
        for candidate in candidates
    ]
    return sorted(scored, key=lambda item: item[1], reverse=True)[:limit]


def load_gazetteer(db_path: Path = DEFAULT_DB) -> dict:
    con = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    districts = [
        row[0]
        for row in con.execute(
            "select d.name from districts d join provinces p on p.id = d.province_id "
            "where p.name like 'Cabo%' order by d.name"
        )
    ]
    posts = [
        (row[0], row[1])
        for row in con.execute(
            "select ap.name, d.name from administrative_posts ap "
            "join districts d on d.id = ap.district_id order by d.name, ap.name"
        )
    ]
    return {"districts": districts, "posts": posts}


def match(value: str, level: str, district: str | None = None, gazetteer: dict | None = None):
    gazetteer = gazetteer or load_gazetteer()
    if level == "district":
        return rank(value, gazetteer["districts"])
    candidates = [
        name for name, d in gazetteer["posts"] if district is None or key(d) == key(district)
    ]
    return rank(value, candidates)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--value", required=True)
    ap.add_argument("--level", choices=["district", "locality", "village"], required=True)
    ap.add_argument("--district", default=None)
    args = ap.parse_args()

    print(f"{args.level}: {args.value!r} district={args.district!r}")
    for name, score in match(args.value, args.level, args.district):
        print(f"  {name:28s} {score:5.1f}")


if __name__ == "__main__":
    main()
