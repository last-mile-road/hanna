"""Rank location combinations from the written district/locality/village names.

The card names are not guaranteed to be internally consistent. This tool scores
every admin/facility chain against all three written names, allowing a name to
match at any level (for example, "Zambezia" in the locality field is a province),
and returns the best chains with coverage, score and margin.

Usage:
  python tools/location_combinations.py --district Chiure --locality Namogelia --village Bilibiza
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from rapidfuzz import fuzz

from location_resolver import key

ROOT = Path(__file__).resolve().parents[1]
ADMIN = ROOT / "countries/mozambique/gazetteer/admin_units.csv"
FACILITIES = ROOT / "countries/mozambique/gazetteer/facilities.csv"

FIELD_WEIGHTS = {
    "province": 0.4,
    "district": 0.8,
    "admin_post": 1.0,
    "locality": 1.1,
    "facility": 1.1,
}
NAME_WEIGHTS = {"district": 0.9, "locality": 1.0, "village": 1.3}
MIN_MATCH = 50.0


@dataclass
class Chain:
    level: str
    name: str
    pcode: str | None
    province: str | None
    district: str | None
    admin_post: str | None
    locality: str | None
    facility: str | None
    lat: float | None
    lon: float | None


def load_chains() -> list[Chain]:
    admin = pd.read_csv(ADMIN)
    facilities = pd.read_csv(FACILITIES)
    chains: list[Chain] = []

    for _, row in admin.iterrows():
        level = {1: "province", 2: "district", 3: "administrative_post", 4: "locality"}[
            int(row["level"])
        ]
        if level == "province":
            continue
        chains.append(
            Chain(
                level=level,
                name=row["name"],
                pcode=row.get("pcode"),
                province=row.get("province"),
                district=row.get("district"),
                admin_post=row.get("admin_post"),
                locality=row.get("locality"),
                facility=None,
                lat=row.get("lat"),
                lon=row.get("lon"),
            )
        )

    for _, row in facilities.iterrows():
        chains.append(
            Chain(
                level="facility",
                name=row["name"],
                pcode=row.get("facility_code"),
                province=row.get("province"),
                district=row.get("district"),
                admin_post=None,
                locality=None,
                facility=row["name"],
                lat=row.get("lat"),
                lon=row.get("lon"),
            )
        )
    return chains


def score_chain(chain: Chain, written: list[tuple[str, str]]):
    matches = []
    for label, value in written:
        if not value:
            continue
        best = None
        for field in ("province", "district", "admin_post", "locality", "facility"):
            candidate = getattr(chain, field)
            if not candidate:
                continue
            score = fuzz.ratio(key(value), key(candidate))
            if best is None or score > best[1]:
                best = (field, score)
        if best and best[1] >= MIN_MATCH:
            field, score = best
            matches.append((label, field, round(score, 1), FIELD_WEIGHTS[field]))
    if not matches:
        return 0.0, matches
    denominator = sum(NAME_WEIGHTS[label] for label, value in written if value)
    numerator = 0.0
    for label, field, score, _ in matches:
        numerator += score * FIELD_WEIGHTS[field] * NAME_WEIGHTS[label]
    final = min(100.0, numerator / denominator) if denominator else 0.0
    return round(final, 1), matches


def best_district(value: str):
    if not value:
        return None
    admin = pd.read_csv(ADMIN)
    districts = admin[admin["level"] == 2]
    scored = [
        (row["name"], fuzz.ratio(key(value), key(row["name"])))
        for _, row in districts.iterrows()
    ]
    scored.sort(key=lambda item: item[1], reverse=True)
    return scored[0] if scored and scored[0][1] >= 85 else None


def rank(district: str, locality: str, village: str, top: int = 5):
    written = [("district", district), ("locality", locality), ("village", village)]
    anchor = best_district(district)
    scored = []
    for chain in load_chains():
        if anchor and key(chain.district) != key(anchor[0]):
            continue
        final, matches = score_chain(chain, written)
        if final:
            scored.append((final, chain, matches))
    scored.sort(key=lambda item: item[0], reverse=True)

    # Deduplicate the same unit reached through duplicate facility rows.
    seen = set()
    out = []
    for final, chain, matches in scored:
        key_id = (chain.level, chain.pcode, chain.name)
        if key_id in seen:
            continue
        seen.add(key_id)
        out.append((final, chain, matches))
        if len(out) >= top:
            break
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--district", default="")
    ap.add_argument("--locality", default="")
    ap.add_argument("--village", default="")
    args = ap.parse_args()

    print(
        f"input: district={args.district!r} locality={args.locality!r} "
        f"village={args.village!r}"
    )
    anchor = best_district(args.district)
    if anchor:
        print(f"district anchor: {anchor[0]!r} ({anchor[1]:.1f})")
    ranked = rank(args.district, args.locality, args.village)
    for i, (final, chain, matches) in enumerate(ranked, 1):
        chain_text = " > ".join(
            x
            for x in (chain.province, chain.district, chain.admin_post, chain.locality or chain.facility)
            if isinstance(x, str) and x.strip()
        )
        match_text = ", ".join(
            f"{label}~{field}:{score:.0f}" for label, field, score, _ in matches
        )
        print(
            f"{i}. {final:5.1f}  {chain.level:18s} {chain_text}\n"
            f"      {chain.lat:.5f},{chain.lon:.5f}  {match_text}"
        )
    if ranked:
        margin = ranked[0][0] - (ranked[1][0] if len(ranked) > 1 else 0)
        print(f"margin over next: {margin:.1f}")


if __name__ == "__main__":
    main()
