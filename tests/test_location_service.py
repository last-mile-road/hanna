"""Tier 2: a service query is bounded, cached, throttled, and never auto-accepted.

No network here. `urllib.request.urlopen` is replaced with a stub, so these tests pin the
containment rules: outside the district, or naming a different district, is not a hit.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from core import location


class FakeResponse:
    def __init__(self, payload):
        self._body = json.dumps(payload).encode()

    def read(self):
        return self._body

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def cache(tmp_path: Path, monkeypatch):
    path = tmp_path / "nominatim-cache.json"
    monkeypatch.setattr(location, "_cache_path", lambda: path)
    monkeypatch.setattr(location, "SERVICE_INTERVAL", 0.0)
    return path


def test_no_service_configured_means_no_request(monkeypatch):
    monkeypatch.setattr(location.settings, "NOMINATIM_URL", "")
    called = []
    monkeypatch.setattr(location.urllib.request, "urlopen", lambda *a, **k: called.append(1))
    assert location.service_search({"place": "Impiri"}, "Balama") == []
    assert called == []


def test_distance_is_sane_for_the_known_chain():
    balama = (-12.9778, 38.3667)
    impiri = (-13.3778, 38.3703)
    assert 40 < location._km(*balama, *impiri) < 50


def test_viewbox_is_left_top_right_bottom_lon_lat():
    box = location._viewbox(-13.0, 38.0, 80)
    left, top, right, bottom = (float(value) for value in box.split(","))
    assert left < 38.0 < right
    assert bottom < -13.0 < top


def test_hits_outside_the_district_or_naming_another_district_are_dropped(cache, monkeypatch):
    payload = [
        {"lat": "-13.38", "lon": "38.37", "name": "Impiri", "display_name": "Impiri, Balama",
         "type": "village", "address": {"county": "Balama"}},
        {"lat": "-16.10", "lon": "38.40", "name": "Impiri", "display_name": "Impiri, Gile",
         "type": "village", "address": {"county": "Gile"}},          # 300 km away
        {"lat": "-13.50", "lon": "38.50", "name": "Impiri", "display_name": "Impiri, Namuno",
         "type": "village", "address": {"county": "Namuno"}},        # different district
    ]
    monkeypatch.setattr(location.urllib.request, "urlopen", lambda *a, **k: FakeResponse(payload))
    hits = location.service_search({"place": "Impiri"}, "Balama", url="http://nominatim.test")
    assert len(hits) == 1
    assert hits[0].name == "Impiri"
    assert hits[0].source == "nominatim"
    assert hits[0].distance_km is not None and hits[0].distance_km < 80


def test_an_answer_is_cached_and_not_asked_twice(cache, monkeypatch):
    calls = []

    def fake(*args, **kwargs):
        calls.append(1)
        return FakeResponse([{"lat": "-13.38", "lon": "38.37", "name": "Impiri",
                              "display_name": "Impiri, Balama", "address": {"county": "Balama"}}])

    monkeypatch.setattr(location.urllib.request, "urlopen", fake)
    first = location.service_search({"place": "Impiri"}, "Balama", url="http://nominatim.test")
    second = location.service_search({"place": "Impiri"}, "Balama", url="http://nominatim.test")
    assert len(calls) == 1
    assert [hit.name for hit in first] == [hit.name for hit in second]


def test_service_hits_reach_review_but_never_auto(cache, monkeypatch):
    def fake(*args, **kwargs):
        return FakeResponse([{"lat": "-13.38", "lon": "38.37", "name": "Noere",
                              "display_name": "Noere, Balama", "address": {"county": "Balama"}}])

    monkeypatch.setattr(location.urllib.request, "urlopen", fake)
    result = location.resolve(district="Balama", locality="Nowhere", place="Nothing",
                              service_url="http://nominatim.test")
    assert result.decision == "review"
    assert result.service_used is True
    assert any(candidate.source == "nominatim" for candidate in result.candidates)


def test_a_service_hit_cannot_rescue_an_unanchored_card_by_itself(cache, monkeypatch):
    def fake(*args, **kwargs):
        return FakeResponse([])

    monkeypatch.setattr(location.urllib.request, "urlopen", fake)
    result = location.resolve(district="Nowhereville", place="Nothing",
                              service_url="http://nominatim.test")
    assert result.decision == "escalate"
