"""Build the Mozambique gazetteer from the current public sources.

Inputs (download separately, not committed):
  - OCHA COD-AB v02, 2025: moz_admin_boundaries.xlsx
  - Mozambique Master Health Facility List: mfl.xlsx

Outputs:
  countries/mozambique/gazetteer/admin_units.csv
  countries/mozambique/gazetteer/facilities.csv
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "countries" / "mozambique" / "gazetteer"


def fix_coord(value, lo: float, hi: float):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if lo <= v <= hi:
        return v
    digits = len(str(abs(int(v))))
    for scale in (10 ** (digits - 2), 1e12, 1e13, 1e6, 1e7):
        candidate = v / scale
        if lo <= candidate <= hi:
            return candidate
    return None


def build_admin(cod_xlsx: Path) -> pd.DataFrame:
    points = pd.read_excel(cod_xlsx, sheet_name="moz_adminpoints")
    rows = []
    for level in (1, 2, 3, 4):
        sheet = pd.read_excel(cod_xlsx, sheet_name=f"moz_admin{level}")
        name_col = f"adm{level}_name"
        pcode_col = f"adm{level}_pcode"
        parent_col = f"adm{level - 1}_pcode" if level > 1 else None
        pts = points[points["admin_level"] == level][[pcode_col, "x_coord", "y_coord"]]
        df = sheet.merge(pts, on=pcode_col, how="left")
        for _, row in df.iterrows():
            rows.append(
                {
                    "level": level,
                    "name": row[name_col],
                    "pcode": row[pcode_col],
                    "parent_pcode": row[parent_col] if parent_col else None,
                    "province": row.get("adm1_name"),
                    "district": row.get("adm2_name"),
                    "admin_post": row.get("adm3_name"),
                    "locality": row.get("adm4_name"),
                    "lat": row["y_coord"],
                    "lon": row["x_coord"],
                    "valid_on": row.get("valid_on"),
                    "version": row.get("version"),
                }
            )
    return pd.DataFrame(rows)


def build_facilities(mfl_xlsx: Path) -> pd.DataFrame:
    df = pd.read_excel(mfl_xlsx, sheet_name="HealthFacilities")
    rows = []
    for _, row in df.iterrows():
        lat = fix_coord(row.get("LAT"), -27.0, -10.0)
        lon = fix_coord(row.get("LON"), 30.0, 42.0)
        rows.append(
            {
                "name": str(row.get("US (Health unit name)") or "").strip(),
                "classification": row.get("CLASSIFICATION"),
                "province": row.get("ADMIN1NAME"),
                "district": row.get("ADMIN2NAMES"),
                "district_code": row.get("ADMIN2CODE"),
                "facility_code": row.get("CUUS (Unique code of health unit)"),
                "lat": lat,
                "lon": lon,
            }
        )
    return pd.DataFrame(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cod", type=Path, required=True)
    ap.add_argument("--mfl", type=Path, required=True)
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    admin = build_admin(args.cod)
    facilities = build_facilities(args.mfl)

    admin.to_csv(OUT / "admin_units.csv", index=False)
    facilities.to_csv(OUT / "facilities.csv", index=False)

    print("admin", len(admin), admin["level"].value_counts().sort_index().to_dict())
    print("admin missing coords", int(admin["lat"].isna().sum()))
    print("facilities", len(facilities), "missing coords", int(facilities["lat"].isna().sum()))
    print("facility sample")
    print(
        facilities[
            facilities["name"].str.contains(
                "Impiri|Bilibiza|Namogelia|Montepuez", case=False, na=False
            )
        ][["name", "district", "lat", "lon"]].head(10).to_string(index=False)
    )


if __name__ == "__main__":
    main()
