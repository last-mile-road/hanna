"""A reviewer's correction teaches the resolver: the alias table.

The loop under test: `record` stores a human-attested mapping, `lookup` returns
it on a later card, and the resolver — fed the canonical name instead of the
misspelling — scores it exactly where fuzzy matching used to guess.

These tests touch the real dev database (`location_aliases`), using spellings
namespaced to this test so they cannot collide with production rows.
"""

from __future__ import annotations

import uuid

from core.aliases import lookup, record
from core.db import connect
from core.location import resolve
from sqlalchemy import text

TEST_KEY = f"zztest{uuid.uuid4().hex[:8]}"  # unique normalised spelling for this run


def test_record_then_lookup_returns_the_attested_unit():
    written = {"district": "Balama", "locality": TEST_KEY, "village": ""}
    chosen = {
        "name": "Impiri", "level": "facility", "pcode": "MZ-FAC-1",
        "lat": -13.3778, "lon": 38.3703,
        "chain": {"district": "Balama", "province": "Cabo Delgado"},
    }
    with connect() as conn:
        rows = record(conn, "mozambique", written, chosen)
        conn.commit()
    assert rows == 2  # one row per non-empty field: district + locality; empty village writes nothing

    hits = lookup("mozambique", {"locality": TEST_KEY.upper() + "  "})
    assert hits["locality"]["canonical"] == "Impiri"
    assert hits["locality"]["level"] == "facility"
    assert hits["locality"]["district"] == "Balama"
    _cleanup()


def test_an_alias_hit_resolves_where_the_gazetteer_escalated():
    # A spelling the gazetteer cannot place (no anchor, no chain clears the floor)
    # resolves through the alias table alone once a reviewer has taught it.
    misspelling = TEST_KEY + "village"
    with connect() as conn:
        record(conn, "mozambique", {"village": misspelling}, {
            "name": "Bilibiza", "level": "facility", "pcode": None,
            "lat": -13.05, "lon": 39.55,
            "chain": {"district": "Chiúre", "province": "Cabo Delgado"},
        })
        conn.commit()

    # The resolver itself is DB-free: run.py substitutes before calling it, so
    # assert the substitution contract — lookup canonicalises, resolve accepts.
    hits = lookup("mozambique", {"village": misspelling})
    canonical = hits["village"]["canonical"]
    result = resolve(district="", locality="", place=canonical)
    # Facility and ADM4 Bilibiza tie, so the cascade correctly refuses to auto-write
    # it — but the alias hit lifts it out of escalate and onto the review map.
    assert result.decision in {"auto", "review"}
    assert result.score >= 85
    assert result.chosen.name == "Bilibiza"
    _cleanup()


def test_record_updates_an_existing_spelling_without_duplicating():
    written = {"village": TEST_KEY + "update"}
    with connect() as conn:
        record(conn, "mozambique", written, {"name": "First", "level": "facility"}, "run-a")
        conn.commit()
        record(conn, "mozambique", written, {"name": "Second", "level": "locality"}, "run-b")
        conn.commit()
        # a repeated post from the same review is not a second attestation
        record(conn, "mozambique", written, {"name": "Second", "level": "locality"}, "run-b")
        conn.commit()
    hits = lookup("mozambique", written)
    assert hits["village"]["canonical"] == "Second"
    with connect() as conn:
        n = conn.execute(
            text("SELECT count(*) FROM location_aliases WHERE written = :w"),
            {"w": TEST_KEY + "update"},
        ).scalar()
        used = conn.execute(
            text("SELECT times_used FROM location_aliases WHERE written = :w"),
            {"w": TEST_KEY + "update"},
        ).scalar()
    assert n == 1
    assert used == 2  # first attestation counts 1; the re-attestation increments, no duplicate row
    _cleanup()


def _cleanup() -> None:
    with connect() as conn:
        conn.execute(text("DELETE FROM location_aliases WHERE written LIKE 'zztest%'"))
        conn.commit()
