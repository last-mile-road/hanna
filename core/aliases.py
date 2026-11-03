"""Location aliases: a reviewer's click teaches the resolver.

The gazetteer is rebuilt from national sources and will never carry every
spelling a hand-written card produces. Fuzzy matching carries some of the load,
but a spelling that once needed a human decision should never need one again.
This module is that memory.

Flow:
    1. `record` — after a reviewer confirms the gazetteer's chain or picks a
       candidate, each written spelling is stored with the canonical unit it
       resolved to (one row per card field).
    2. `lookup` — before resolving a new card, each written spelling is checked
       here first. A hit is an exact, human-attested mapping, so the resolver
       scores it at 100 instead of guessing with fuzzy matching.
    3. `run.py` applies the lookup to the names it passes to `core.location`
       and marks the resolution `aliases_applied`; `server/app.py` records new
       rows when a review lands.

Nothing here invents a mapping: only a human decision writes a row, and the
run that produced it stays on the row as provenance.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

from location_resolver import key  # noqa: E402

# Card field -> the location dict field run.py reads it from.
LOCATION_CONCEPTS = {"district": "district", "locality": "locality", "village": "village_name"}


def lookup(country: str, written: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Human-attested canonical units for the written spellings.

    Returns {field: {canonical, level, pcode, district, province, lat, lon}} for
    every card field whose normalised spelling has an alias row. Never raises:
    a missing table or a down database must leave resolution as it was.
    """
    wanted = {field: key(value) for field, value in written.items() if (value or "").strip()}
    if not wanted:
        return {}
    try:
        from .db import connect

        hits: dict[str, dict[str, Any]] = {}
        with connect() as conn:
            for field, normalised in wanted.items():
                row = conn.execute(
                    text(
                        "SELECT canonical, level, pcode, district, province, lat, lon "
                        "FROM location_aliases "
                        "WHERE country = :country AND written = :written AND field = :field"
                    ),
                    {"country": country, "written": normalised, "field": field},
                ).mappings().first()
                if row:
                    hits[field] = dict(row)
        return hits
    except Exception:
        return {}


def record(conn: Connection, country: str, written: dict[str, str],
           chosen: dict[str, Any], run_id: str | None = None) -> int:
    """Store the spellings a reviewer just resolved, one row per card field.

    `written` is {district, locality, village}; `chosen` is the unit the human
    attested (the gazetteer's chosen candidate on confirm, the picked candidate
    on choose), with its full chain. Each field teaches its own level: the
    district spelling maps to the chain's district, the locality and village
    spellings map to the chosen unit itself. A spelling already on file is
    updated to the newest attestation and the use counter is incremented —
    the evidence of what happened stays in `runs.location`. Returns the number
    of rows written.
    """
    unit = chosen.get("canonical") or chosen.get("name")
    if not unit:
        return 0
    chain = chosen.get("chain") or {}
    field_targets = {
        # field      -> (canonical, level, pcode, lat, lon)
        "district": (chain.get("district") or unit, "district", None, None, None),
        "locality": (unit, chosen.get("level"), chosen.get("pcode"), chosen.get("lat"), chosen.get("lon")),
        "village": (unit, chosen.get("level"), chosen.get("pcode"), chosen.get("lat"), chosen.get("lon")),
    }
    rows = 0
    for field, value in written.items():
        raw = (value or "").strip()
        if not raw:
            continue
        canonical, level, pcode, lat, lon = field_targets[field]
        conn.execute(
            text(
                "INSERT INTO location_aliases "
                "  (country, written, written_raw, field, level, canonical, pcode, "
                "   district, province, lat, lon, run_id, source, times_used) "
                "VALUES (:country, :written, :written_raw, :field, :level, :canonical, "
                "        :pcode, :district, :province, :lat, :lon, :run_id, 'reviewer', 1) "
                "ON CONFLICT (country, written, field) DO UPDATE SET "
                "  written_raw = EXCLUDED.written_raw, "
                "  level = EXCLUDED.level, canonical = EXCLUDED.canonical, "
                "  pcode = EXCLUDED.pcode, district = EXCLUDED.district, "
                "  province = EXCLUDED.province, lat = EXCLUDED.lat, lon = EXCLUDED.lon, "
                "  run_id = EXCLUDED.run_id, updated_at = now(), "
                "  times_used = location_aliases.times_used + 1"
            ),
            {
                "country": country,
                "written": key(raw),
                "written_raw": raw,
                "field": field,
                "level": level,
                "canonical": canonical,
                "pcode": pcode,
                "district": chain.get("district"),
                "province": chain.get("province"),
                "lat": lat,
                "lon": lon,
                "run_id": run_id,
            },
        )
        rows += 1
    return rows
