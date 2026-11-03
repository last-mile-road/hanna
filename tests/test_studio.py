"""Studio API: a country team defines its card without touching the live pack.

The invariants under test:
  - the live form.draft.yaml is never written by a Studio save;
  - a saved draft round-trips and validates against the real rules;
  - a mid-edit draft returns its errors to the screen, it does not 500.
"""

from __future__ import annotations

import shutil

import pytest
import yaml
from fastapi.testclient import TestClient

from core import settings
from server.app import app

client = TestClient(app)


@pytest.fixture()
def studio_country(tmp_path, monkeypatch):
    """A scratch country with the mozambique template, no packs."""
    original = settings.COUNTRIES_DIR
    source = original / "mozambique"
    target = tmp_path / "zzstudio_test"
    target.mkdir()
    shutil.copy(source / "template.jpg", target / "template.jpg")
    monkeypatch.setattr(settings, "COUNTRIES_DIR", tmp_path)
    yield "zzstudio_test"


def test_pack_of_an_unknown_country_is_404():
    assert client.get("/api/studio/zz_unknown/pack").status_code == 404


def test_new_country_creates_a_skeleton_and_pack_reads_back(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "COUNTRIES_DIR", tmp_path)
    response = client.post("/api/studio/countries", json={"name": "zz Studio Test"})
    assert response.status_code == 200
    country = response.json()["country"]

    pack = client.get(f"/api/studio/{country}/pack").json()
    assert pack["form"]["country"] == country
    assert pack["blocks"] == []
    assert any(c["id"] == "patient_name" for c in pack["concepts"])
    # a skeleton without a reference year is honest about it
    assert pack["form"].get("reference_year") is None


def test_save_round_trips_and_the_live_pack_is_untouched(studio_country, monkeypatch):
    country = studio_country
    real_countries = settings.COUNTRIES_DIR  # the scratch dir, thanks to the fixture
    donor_live = original_countries() / "mozambique" / "form.draft.yaml"
    live_before = donor_live.read_text()

    draft = {
        "form": {"id": "zz_test_card", "reference_year": 2026},
        "blocks": [{
            "id": "demographics", "block_type": "value_pairs",
            "bbox": [10, 10, 1000, 400],
            "value_pairs": [{
                "id": "patient_name", "concept": "patient_name",
                "bbox": [20, 20, 900, 120], "label_local": "Nome do doente",
            }],
        }],
    }
    response = client.put(f"/api/studio/{country}/draft", json=draft)
    assert response.status_code == 200
    assert response.json()["errors"] == []

    # the saved draft loads through the real loader — the acceptance test
    from core.forms import load_form_pack

    pack = load_form_pack(country, "form.studio.yaml")
    assert pack.pairs[0].label_local == "Nome do doente"

    # Studio wrote its own draft only: no live pack anywhere
    assert (real_countries / country / "form.studio.yaml").exists()
    assert not (real_countries / country / "form.draft.yaml").exists()
    assert donor_live.read_text() == live_before


def original_countries():
    """The real countries dir, even while a test has patched settings."""
    from core import settings as s

    return s.ROOT / "countries"


def test_save_preserves_keys_the_editor_does_not_edit(studio_country):
    """allowed_values, derived, geometry_source, source_group must survive a
    Studio round-trip — the pipeline reads them, the editor does not show them."""
    country = studio_country
    draft = {
        "form": {"id": "zz_test_card", "reference_year": 2026},
        "blocks": [{
            "id": "demographics", "block_type": "value_pairs",
            "bbox": [10, 10, 1000, 400], "source_group": "General_demographic",
            "value_pairs": [{
                "id": "gender", "concept": "gender", "bbox": [20, 20, 120, 90],
                "allowed_values": ["M", "F"], "derived": False,
                "geometry_source": "converted",
            }],
        }],
    }
    response = client.put(f"/api/studio/{country}/draft", json=draft)
    assert response.status_code == 200
    saved = yaml.safe_load((settings.COUNTRIES_DIR / country / "form.studio.yaml").read_text())
    block = saved["blocks"][0]
    assert block["source_group"] == "General_demographic"
    pair = block["value_pairs"][0]
    assert pair["allowed_values"] == ["M", "F"]
    assert pair["derived"] is False
    assert pair["geometry_source"] == "converted"


def test_a_draft_with_errors_returns_them_not_a_500(studio_country):
    country = studio_country
    draft = {
        "form": {"id": "zz_test_card", "reference_year": 2026},
        "blocks": [{
            "id": "demographics", "block_type": "no_such_type",
            "bbox": [10, 10, 1000, 400],
            "value_pairs": [{
                "id": "orphan", "concept": "not_a_concept",
                "bbox": [50, 50, 60, 60],
            }],
        }],
    }
    response = client.put(f"/api/studio/{country}/draft", json=draft)
    assert response.status_code == 200
    errors = response.json()["errors"]
    assert any("no_such_type" in e for e in errors)
    assert any("not_a_concept" in e for e in errors)

    pack = client.get(f"/api/studio/{country}/pack").json()
    assert pack["errors"] == errors  # the screen sees the same list


def test_template_upload_rejects_a_non_image(studio_country):
    response = client.post(
        f"/api/studio/{studio_country}/template",
        files={"file": ("card.txt", b"not an image", "text/plain")},
    )
    assert response.status_code == 422
