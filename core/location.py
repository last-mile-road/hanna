"""Location cascade: gazetteer first, constrained service second, human third.

Implements `countries/<country>/location.yaml` on top of the two existing tools:
`location_resolver.py` (one name to candidates) and `location_combinations.py` (whole
chains against all written names). Nothing here re-implements matching; it decides.

The three written names are not guaranteed to be consistent, and on the test cards they
are fiction. So the cascade ranks *chains* and refuses to answer when the chain does not
corroborate itself:

    auto       score >= 85 and the runner-up is >= 10 behind
    review     score >= 70, or a tie, or a thin margin  -> show the alternatives
    escalate   below 70                                 -> service, then human

A district is the anchor. A facility match outranks everything because the card prints
"Unidade Sanitária", and a facility point is accurate to about 250 m.

    from core.location import resolve
    result = resolve(district="Balama", locality="Impiri", place="Impiri")
    result.decision, result.level, result.lat, result.lon
"""

from __future__ import annotations

import json
import math
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import location_combinations as combos  # noqa: E402
from rapidfuzz import fuzz  # noqa: E402

from . import settings  # noqa: E402

AUTO_SCORE = 85.0
REVIEW_SCORE = 70.0
MIN_MARGIN = 10.0
TIE = 0.05

# Tier 2: a service query carries a place name, so it is opt-in, cached, rate-limited and
# constrained to the district. A hit from a service is never auto-accepted: it goes to the
# map picker, because a service answers "a place with this name exists", not "this is the
# place on the card".
SERVICE_MAX_KM = 80.0
SERVICE_TIMEOUT = 12
SERVICE_INTERVAL = 1.0          # Nominatim's public policy: one request per second
_SERVICE_LOCK = threading.Lock()
_last_service_call = 0.0

# How precise the answer is at each level, used by the review map to size its ring.
PRECISION_METRES = {
    "facility": 250,
    "locality": 1500,
    "administrative_post": 4000,
    "district": 12000,
    "province": 60000,
}


@dataclass
class Candidate:
    """One chain the gazetteer can offer, with the evidence behind it."""

    score: float
    level: str
    name: str
    lat: float | None
    lon: float | None
    chain: dict[str, Any] = field(default_factory=dict)
    matches: list[dict[str, Any]] = field(default_factory=list)
    source: str = "gazetteer"
    distance_km: float | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class Resolution:
    decision: str                      # auto | review | escalate
    level: str | None = None
    lat: float | None = None
    lon: float | None = None
    score: float = 0.0
    margin: float = 0.0
    precision_m: int | None = None
    anchored: bool = False
    district_anchor: str | None = None
    reason: str = ""
    service_used: bool = False
    chosen: Candidate | None = None
    candidates: list[Candidate] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["chosen"] = self.chosen.as_dict() if self.chosen else None
        return data


def _candidate(score: float, chain, matches) -> Candidate:
    return Candidate(
        score=score,
        level=chain.level,
        name=chain.facility or chain.locality or chain.name,
        lat=chain.lat,
        lon=chain.lon,
        chain={
            "province": chain.province,
            "district": chain.district,
            "administrative_post": chain.admin_post,
            "locality": chain.locality,
            "facility": chain.facility,
        },
        matches=[{"written": label, "matched": field_name, "score": round(value, 1)}
                 for label, field_name, value, _ in matches],
    )


def _order(candidates: list[Candidate]) -> list[Candidate]:
    """Score first, then a facility over a locality, then the chain that matched more names.

    `location_combinations.rank` sorts on score alone, so sibling admin posts can come back
    in an arbitrary order at identical scores. The margin rule then sends them to review,
    which is right, but the *first* alternative shown should still be the better unit: the
    card names a health facility more often than a locality.
    """
    return sorted(
        candidates,
        key=lambda c: (c.score, c.level == "facility", len(c.matches)),
        reverse=True,
    )


def _centre(district: str) -> tuple[float, float] | None:
    """District centroid from the gazetteer, used to bound a service query."""
    anchor = combos.best_district(district)
    if not anchor:
        return None
    rows = combos._districts()
    row = rows[rows["name"] == anchor[0]]
    if row.empty:
        return None
    return float(row.iloc[0]["lat"]), float(row.iloc[0]["lon"])


def _km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius = 6371.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * radius * math.asin(math.sqrt(a))


def _viewbox(lat: float, lon: float, km: float) -> str:
    """Nominatim wants left,top,right,bottom in degrees."""
    dlat = km / 111.0
    dlon = km / max(1e-6, 111.0 * math.cos(math.radians(lat)))
    return f"{lon - dlon:.5f},{lat + dlat:.5f},{lon + dlon:.5f},{lat - dlat:.5f}"


def _cache_path() -> Path:
    return settings.OUTPUT_DIR / "nominatim-cache.json"


def _cache_read() -> dict[str, Any]:
    try:
        return json.loads(_cache_path().read_text())
    except (OSError, ValueError):
        return {}


def _cache_write(cache: dict[str, Any]) -> None:
    try:
        _cache_path().parent.mkdir(parents=True, exist_ok=True)
        _cache_path().write_text(json.dumps(cache, ensure_ascii=False, indent=1))
    except OSError:
        pass  # a cache that cannot be written must not fail a card


def _throttle() -> None:
    global _last_service_call
    with _SERVICE_LOCK:
        wait = SERVICE_INTERVAL - (time.monotonic() - _last_service_call)
        if wait > 0:
            time.sleep(wait)
        _last_service_call = time.monotonic()


def service_search(names: dict[str, str], district: str, province: str | None = None,
                   url: str | None = None, limit: int = 5) -> list[Candidate]:
    """Tier 2: ask a Nominatim-compatible service, bounded to the district.

    Returns candidates marked `source="nominatim"`. Never raises: a service that is down
    or slow must leave the card in the same state it was, not fail it.
    """
    base = (url if url is not None else settings.NOMINATIM_URL or "").rstrip("/")
    if not base:
        return []
    place = (names.get("place") or "").strip()
    locality = (names.get("locality") or "").strip()
    query_name = place or locality
    if not query_name:
        return []

    parts = [query_name]
    if district:
        parts.append(district)
    if province:
        parts.append(province)
    parts.append("Mozambique")
    query = ", ".join(parts)

    centre = _centre(district)
    params = {
        "q": query,
        "format": "jsonv2",
        "addressdetails": "1",
        "limit": str(limit),
        "countrycodes": "mz",
        "accept-language": "pt",
    }
    if centre:
        params["viewbox"] = _viewbox(centre[0], centre[1], SERVICE_MAX_KM)
        params["bounded"] = "1"

    cache_key = f"{base}|{query}|{centre[0]:.3f},{centre[1]:.3f}" if centre else f"{base}|{query}"
    cache = _cache_read()
    entry = cache.get(cache_key)
    payload = entry.get("results") if isinstance(entry, dict) else entry
    if payload is None:
        request = urllib.request.Request(
            f"{base}/search?" + urllib.parse.urlencode(params),
            headers={"User-Agent": "HANNA/0.1 (leprosy card location check)"},
        )
        try:
            _throttle()
            with urllib.request.urlopen(request, timeout=SERVICE_TIMEOUT) as response:
                payload = json.load(response)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError):
            return []
        cache[cache_key] = {
            "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "results": payload,
        }
        _cache_write(cache)

    written = [value for value in names.values() if value]
    hits: list[Candidate] = []
    for result in payload if isinstance(payload, list) else []:
        try:
            lat, lon = float(result["lat"]), float(result["lon"])
        except (KeyError, TypeError, ValueError):
            continue
        distance = _km(centre[0], centre[1], lat, lon) if centre else None
        if distance is not None and distance > SERVICE_MAX_KM:
            continue  # the service answered outside the district; not our place
        address = result.get("address") or {}
        named = (address.get("county") or address.get("city") or address.get("municipality")
                 or address.get("state_district") or address.get("town"))
        if named and district:
            if fuzz.token_set_ratio(combos.key(district), combos.key(named)) < 75:
                continue  # a different district, whatever the name looks like
        label = result.get("name") or result.get("display_name", "").split(",")[0]
        score = max((fuzz.ratio(combos.key(value), combos.key(label)) for value in written),
                    default=0.0)
        hits.append(Candidate(
            score=round(score, 1),
            level="service",
            name=label,
            lat=lat,
            lon=lon,
            chain={"display_name": result.get("display_name"),
                   "type": result.get("type"), "district": named},
            matches=[],
            source="nominatim",
            distance_km=round(distance, 1) if distance is not None else None,
        ))
    return _order(hits)


def _gazetteer_resolve(district: str = "", locality: str = "", place: str = "",
                       province: str | None = None, top: int = 5) -> Resolution:
    """Tier 1: decide from the gazetteer alone. `resolve` adds tier 2 on an escalate."""
    """Resolve the three written names into one decision, with its alternatives."""
    written = {"district": district or "", "locality": locality or "", "place": place or ""}
    if not any(written.values()):
        return Resolution(decision="escalate", reason="no location written on the card")

    anchor = combos.best_district(district)
    anchored = anchor is not None
    if not anchored and province:
        # A province on the card narrows nothing by itself, but it can re-anchor through
        # a district written in the locality field; the chain ranking already allows that.
        written["province"] = province

    ranked = combos.rank(district, locality, place, top=top)
    if not ranked:
        return Resolution(
            decision="escalate",
            anchored=anchored,
            district_anchor=anchor[0] if anchor else None,
            reason="no chain in the gazetteer matches any written name",
        )

    candidates = _order([_candidate(score, chain, matches) for score, chain, matches in ranked])
    if not anchored:
        # Without a district to anchor on, the chain ranking searches all 3,920 rows and
        # fuzzy matching at 50 returns plausible-looking rubbish: "Nowhere" matches "Noere"
        # at 83 on a short string. Keep a candidate only if the whole chain clears the
        # review floor, otherwise offer nothing and escalate.
        candidates = [candidate for candidate in candidates if candidate.score >= REVIEW_SCORE]
        if not candidates:
            return Resolution(
                decision="escalate",
                anchored=False,
                reason="no district anchor and no chain clears the review floor",
            )
    best = candidates[0]
    margin = round(best.score - candidates[1].score, 1) if len(candidates) > 1 else best.score

    resolution = Resolution(
        decision="escalate",
        level=best.level,
        lat=best.lat,
        lon=best.lon,
        score=best.score,
        margin=margin,
        precision_m=PRECISION_METRES.get(best.level),
        anchored=anchored,
        district_anchor=anchor[0] if anchor else None,
        chosen=best,
        candidates=candidates,
    )

    if best.lat is None or best.lon is None:
        resolution.reason = "best chain has no coordinates"
        return resolution
    if best.score < REVIEW_SCORE:
        resolution.decision = "escalate"
        resolution.reason = f"best chain scores {best.score:.1f}, below the review floor {REVIEW_SCORE:.0f}"
        return resolution
    if margin <= TIE:
        resolution.decision = "review"
        resolution.reason = "two or more chains tie; a person has to choose"
        return resolution
    if best.score < AUTO_SCORE:
        resolution.decision = "review"
        resolution.reason = f"best chain scores {best.score:.1f}, below the auto floor {AUTO_SCORE:.0f}"
        return resolution
    if margin < MIN_MARGIN:
        resolution.decision = "review"
        resolution.reason = f"margin of {margin:.1f} is under {MIN_MARGIN:.0f}; the runner-up is plausible"
        return resolution

    resolution.decision = "auto"
    resolution.reason = f"{best.level} match at {best.score:.1f}, {margin:.1f} clear of the next chain"
    return resolution


def resolve(district: str = "", locality: str = "", place: str = "",
            province: str | None = None, top: int = 5,
            service_url: str | None = None) -> Resolution:
    """The cascade: gazetteer first, then a bounded service query, and no auto from a service.

    A service hit is evidence that a place with that name exists near the district, not
    that it is the place written on the card, so the decision it can reach is `review`.
    """
    resolution = _gazetteer_resolve(district, locality, place, province=province, top=top)
    if resolution.decision != "escalate":
        return resolution

    hits = service_search(
        {"district": district, "locality": locality, "place": place},
        district,
        province,
        url=service_url,
    )
    if not hits:
        return resolution

    resolution.candidates.extend(hits)
    resolution.service_used = True
    resolution.decision = "review"
    resolution.reason = (
        f"gazetteer: {resolution.reason}; {len(hits)} service candidate(s) "
        f"inside {resolution.district_anchor or district or 'the area'} to confirm on the map"
    )
    return resolution


def summary(resolution: Resolution) -> str:
    """One line for a log or a review header."""
    if resolution.decision == "auto" and resolution.chosen:
        chain = " > ".join(
            value for value in (
                resolution.chosen.chain.get("province"),
                resolution.chosen.chain.get("district"),
                resolution.chosen.chain.get("administrative_post"),
                resolution.chosen.chain.get("locality"),
                resolution.chosen.chain.get("facility"),
            ) if value
        )
        return f"auto · {resolution.level} · {chain} · {resolution.score:.1f}"
    return f"{resolution.decision} · {resolution.reason}"
