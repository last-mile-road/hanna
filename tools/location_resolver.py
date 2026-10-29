"""Prototype resolver for the Mozambique country pack.

Reads the COD-AB admin units and the MFL facilities, matches in Portuguese, and
returns candidates with level, p-code, coordinates and score. No database or API.

Usage:
  python tools/location_resolver.py --district Chiure --locality Namogelia --place Bilibiza
"""

from __future__ import annotations

import argparse
import re
import unicodedata
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT / "countries/mozambique/gazetteer/admin_units.csv"
FACILITIES = ROOT / "countries/mozambique/gazetteer/facilities.csv"

PREFIXES = (
    "posto administrativo de ",
    "posto administrativo ",
    "localidade de ",
    "localidade ",
    "aldeia de ",
    "aldeia ",
    "vila de ",
    "vila ",
    "bairro de ",
    "bairro ",
    "centro de saude de ",
    "centro de saude ",
    "unidade sanitaria de ",
    "unidade sanitaria ",
)


def key(value) -> str:
    text = unicodedata.normalize("NFKD", str(value or ""))
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.lower().replace("ç", "c")
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    text = " ".join(text.split())
    for prefix in PREFIXES:
        if text.startswith(prefix):
            text = text[len(prefix) :]
            break
    return text


def rank(df: pd.DataFrame, column: str, value: str, filters: dict | None = None, n: int = 3):
    if not value:
        return []
    subset = df
    for col, wanted in (filters or {}).items():
        if wanted:
            subset = subset[subset[col].map(key) == key(wanted)]
    if subset.empty:
        return []
    scored = [
        (row, fuzz.ratio(key(value), key(row[column])))
        for _, row in subset.iterrows()
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[:n]


def candidate(row, level, source, score, name_col="name"):
    return {
        "level": level,
        "name": row.get(name_col),
        "pcode": row.get("pcode") or row.get("facility_code"),
        "lat": row.get("lat"),
        "lon": row.get("lon"),
        "province": row.get("province"),
        "district": row.get("district"),
        "admin_post": row.get("admin_post"),
        "score": round(float(score), 1),
        "source": source,
    }


def resolve(district: str, locality: str = "", place: str = ""):
    admin = pd.read_csv(ADMIN)
    facilities = pd.read_csv(FACILITIES)

    districts = admin[admin["level"] == 2]
    d_hits = rank(districts, "name", district)
    if not d_hits:
        return []
    d_row, d_score = d_hits[0]
    province = d_row["province"]
    matched_district = d_row["name"]

    out = [candidate(d_row, "district", "COD-AB", d_score)]

    a3_hits = rank(
        admin[admin["level"] == 3],
        "name",
        locality,
        {"province": province, "district": matched_district},
    )
    for row, score in a3_hits:
        out.append(candidate(row, "administrative_post", "COD-AB", score))
    if locality:
        for row, score in rank(
            facilities,
            "name",
            locality,
            {"province": province, "district": matched_district},
        ):
            out.append(candidate(row, "facility", "MFL", score))

    facility_hits = rank(
        facilities,
        "name",
        place,
        {"province": province, "district": matched_district},
    )
    for row, score in facility_hits:
        out.append(candidate(row, "facility", "MFL", score))

    for row, score in rank(
        admin[admin["level"] == 3],
        "name",
        place,
        {"province": province, "district": matched_district},
    ):
        out.append(candidate(row, "administrative_post", "COD-AB", score))

    adm3_name = a3_hits[0][0]["name"] if a3_hits else None
    a4_hits = rank(
        admin[admin["level"] == 4],
        "name",
        place,
        {
            "province": province,
            "district": matched_district,
            "admin_post": adm3_name,
        },
    )
    if not a4_hits and place:
        a4_hits = rank(
            admin[admin["level"] == 4],
            "name",
            place,
            {"province": province, "district": matched_district},
        )
    for row, score in a4_hits:
        out.append(candidate(row, "locality", "COD-AB", score))

    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--district", required=True)
    ap.add_argument("--locality", default="")
    ap.add_argument("--place", default="")
    args = ap.parse_args()

    print(f"input: district={args.district!r} locality={args.locality!r} place={args.place!r}")
    for item in resolve(args.district, args.locality, args.place):
        print(
            f"  {item['level']:18s} {str(item['name']):28s} {str(item['pcode']):14s} "
            f"{item['lat']:9.5f} {item['lon']:9.5f}  {item['score']:5.1f}  {item['source']}"
        )


if __name__ == "__main__":
    main()
