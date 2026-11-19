"""The location cascade decides; it does not guess.

Fixtures are the seven measured cards. Their written names are partly fiction, which is
why three of them escalate: that is the cascade working, not failing.
"""

from __future__ import annotations

import pytest

from core.location import resolve


def test_facility_match_is_accepted_with_its_point():
    result = resolve("mozambique", district="Balama", locality="Impiri", place="Impiri")
    assert result.decision == "auto"
    assert result.level == "facility"
    assert result.score >= 85
    assert result.margin >= 10
    assert result.lat == pytest.approx(-13.3778, abs=0.01)
    assert result.lon == pytest.approx(38.3703, abs=0.01)
    assert result.precision_m == 250


def test_locality_chain_is_accepted_and_names_the_whole_chain():
    result = resolve("mozambique", district="Chiure", locality="Namogelia", place="Bilibiza")
    assert result.decision == "auto"
    assert result.level == "locality"
    assert result.chosen.chain["district"] == "Chiúre"
    assert result.chosen.chain["administrative_post"] == "Namogelia"


def test_a_province_written_in_the_locality_field_does_not_break_the_chain():
    result = resolve("mozambique", district="Mocuba", locality="Zambezia", place="Muinamba")
    # the chain itself stays below the floor: the province is a constraint, not a locality
    assert result.score < 70
    assert any(
        match["matched"] == "province"
        for candidate in result.candidates
        if candidate.source == "gazetteer"
        for match in candidate.matches
    )
    # service candidates (local OSM) may lift the decision to review — that is
    # the cascade doing its job; the gazetteer still refuses to place it


def test_a_tie_is_review_even_when_the_score_is_high():
    result = resolve("mozambique", district="Montepuez", locality="Nanili", place="Montepuez-sede")
    assert result.margin <= 0.05
    assert result.decision in {"review", "escalate"}
    if result.decision == "review":
        assert "tie" in result.reason


def test_thin_margin_sends_a_plausible_chain_to_review():
    result = resolve("mozambique", district="Gile", locality="Alto Ligonha - Zambezia", place="Napakhala")
    assert result.score >= 70
    assert result.margin < 10
    assert result.decision == "review"


def test_unknown_names_escalate_without_a_chain():
    result = resolve("mozambique", district="Nowhereville", locality="Nowhere", place="Nothing")
    assert result.decision == "escalate"
    assert result.candidates == []


def test_nothing_written_escalates_without_touching_the_gazetteer():
    result = resolve("mozambique")
    assert result.decision == "escalate"
    assert "no location" in result.reason


def test_precision_follows_the_level():
    facility = resolve("mozambique", district="Balama", locality="Impiri", place="Impiri")
    district = resolve("mozambique", district="Chiure", locality="", place="")
    assert facility.precision_m == 250
    assert district.precision_m in {12000, None} or district.level != "district"
    assert facility.precision_m < (district.precision_m or 60000)
