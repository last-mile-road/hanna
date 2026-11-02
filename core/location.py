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

import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

TOOLS = Path(__file__).resolve().parents[1] / "tools"
if str(TOOLS) not in sys.path:
    sys.path.insert(0, str(TOOLS))

import location_combinations as combos  # noqa: E402

AUTO_SCORE = 85.0
REVIEW_SCORE = 70.0
MIN_MARGIN = 10.0
TIE = 0.05

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


def resolve(district: str = "", locality: str = "", place: str = "",
            province: str | None = None, top: int = 5) -> Resolution:
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
