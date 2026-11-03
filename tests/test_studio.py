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
    """A scratch country with the mozambique template, no packs.

    The publish test touches the real ledger (it loads a form), so the
    teardown removes everything this fixture's country put there."""
    original = settings.COUNTRIES_DIR
    source = original / "mozambique"
    target = tmp_path / "zzstudio_test"
    target.mkdir()
    shutil.copy(source / "template.jpg", target / "template.jpg")
    monkeypatch.setattr(settings, "COUNTRIES_DIR", tmp_path)
    yield "zzstudio_test"
    # ledger cleanup: the scratch country's forms, its country row, and any
    # runs that referenced its forms
    from core.db import connect as _connect
    from sqlalchemy import text as _text

    with _connect() as conn:
        conn.execute(_text(
            "delete from runs where form_id in "
            "  (select f.id from forms f join countries c on c.id = f.country_id "
            "   where c.code = 'zzstudio_test')"
        ))
        conn.execute(_text("delete from forms where form_key like 'zzstudio%'"))
        conn.execute(_text("delete from countries where code = 'zzstudio_test'"))
        conn.commit()


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
        "pairs": [{
            "id": "vp_001", "concept": "patient_name",
            "bbox": [20, 20, 900, 120], "label_local": "Nome do doente",
            "description": "Nome completo do doente no cartão",
            "required": True, "required_hint": "Pergunte ao clínico se o nome está escrito",
        }],
    }
    response = client.put(f"/api/studio/{country}/draft", json=draft)
    assert response.status_code == 200
    assert response.json()["errors"] == []

    # the country drew pairs only; the server wrapped each in a provisional block
    saved = yaml.safe_load((real_countries / country / "form.studio.yaml").read_text())
    auto = saved["blocks"][0]
    assert auto["id"] == "auto_vp_001"
    assert auto["studio_auto"] is True
    assert auto["value_pairs"][0]["label_local"] == "Nome do doente"
    assert auto["value_pairs"][0]["required"] is True
    assert auto["value_pairs"][0]["description"].startswith("Nome completo")

    # and the wrapped draft loads through the real loader — the acceptance test
    from core.forms import load_form_pack

    pack = load_form_pack(country, "form.studio.yaml")
    assert pack.pairs[0].label_local == "Nome do doente"

    # Studio wrote its own draft only: no live pack anywhere
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


def test_a_second_form_variant_keeps_its_own_template(studio_country):
    """The back side of a card is its own form: own draft, own template, no
    bleed-through of the front's pairs. (Found when the back side was uploaded
    over the front template and the front's bboxes drew on top of it.)"""
    country = studio_country
    client.post(f"/api/studio/{country}/forms", json={"variant": "back"})
    pack = client.get(f"/api/studio/{country}/pack?variant=back").json()
    assert pack["form"]["template"] == "template.back.jpg"
    assert pack["blocks"] == []

    # a save for the back variant writes form.back.studio.yaml only
    response = client.put(f"/api/studio/{country}/draft?variant=back", json={
        "form": {"id": "zz_back", "reference_year": 2026},
        "pairs": [{"id": "vp_001", "concept": "patient_name", "bbox": [10, 10, 300, 90]}],
    })
    assert response.status_code == 200
    import yaml
    assert (settings.COUNTRIES_DIR / country / "form.back.studio.yaml").exists()
    assert not (settings.COUNTRIES_DIR / country / "form.draft.yaml").exists()

    # the template endpoint serves each variant's own image — after one exists
    assert client.get(f"/api/studio/{country}/template?variant=back").status_code == 404
    import cv2 as _cv
    import numpy as _np

    _ok, _jpg = _cv.imencode(".jpg", _np.full((600, 900, 3), 240, dtype=_np.uint8))
    up = client.post(f"/api/studio/{country}/template?variant=back",
                     files={"file": ("back.jpg", _jpg.tobytes(), "image/jpeg")})
    assert up.status_code == 200
    assert up.json()["template"] == "template.back.jpg"
    assert client.get(f"/api/studio/{country}/template?variant=back").status_code == 200
    # the front variant's template is untouched
    assert client.get(f"/api/studio/{country}/template").status_code == 200


def test_publish_freezes_and_loads_the_ledger(studio_country):
    """A1: the complete loop — draft validates, freezes as the live pack, and
    appears in the ledger as a published version a run can select."""
    country = studio_country
    from sqlalchemy import text as _text
    from core.db import connect as _connect
    with _connect() as conn:
        base = conn.execute(
            _text("SELECT coalesce(max(version), 0) FROM forms WHERE form_key = :k"),
            {"k": f"{country}_card"},
        ).scalar()
    client.put(f"/api/studio/{country}/draft", json={
        "form": {"id": f"{country}_card", "reference_year": 2026},
        "pairs": [{
            "id": "vp_001", "concept": "patient_name", "bbox": [20, 20, 900, 120],
            "label_local": "Nome do doente",
        }],
    })
    response = client.post(f"/api/studio/{country}/publish?variant=draft")
    assert response.status_code == 200, response.json()
    body = response.json()
    assert body["form_key"] == f"{country}_card"
    assert body["version"] == base + 1
    assert (settings.COUNTRIES_DIR / country / "form.draft.yaml").exists()

    # a draft with problems refuses to publish
    client.put(f"/api/studio/{country}/draft", json={
        "form": {"id": f"{country}_card", "reference_year": 2026},
        "pairs": [{"id": "vp_bad", "concept": "", "bbox": [1, 1, 50, 50]}],
    })
    refused = client.post(f"/api/studio/{country}/publish?variant=draft")
    assert refused.status_code == 422

    # re-publishing the good draft bumps the version
    client.put(f"/api/studio/{country}/draft", json={
        "form": {"id": f"{country}_card", "reference_year": 2026},
        "pairs": [{
            "id": "vp_001", "concept": "patient_name", "bbox": [20, 20, 900, 120],
            "label_local": "Nome do doente",
        }],
    })
    again = client.post(f"/api/studio/{country}/publish?variant=draft").json()
    assert again["version"] == base + 2

    # run creation matches the published variant: a run for this country picks
    # this form, not the global latest
    from server.app import _form
    from core.db import connect as _connect
    with _connect() as conn:
        cid = conn.execute(_text("select id from countries where code = :c"),
                           {"c": country}).scalar()
        row = _form(conn, cid, "draft")
        assert row is not None and row["form_key"] == f"{country}_card"
        assert row["version"] == base + 2


def test_a_pair_with_no_concept_yet_is_an_error_not_a_500(studio_country):
    """The live bug the old server log caught: an unlinked pair reached the
    cardinality check and crashed it. A country draft mid-edit must save and
    come back with the problem listed."""
    country = studio_country
    draft = {
        "form": {"id": "zz_test_card", "reference_year": 2026},
        "pairs": [{"id": "vp_001", "concept": "", "bbox": [10, 10, 300, 90]}],
    }
    response = client.put(f"/api/studio/{country}/draft", json=draft)
    assert response.status_code == 200
    errors = response.json()["errors"]
    assert any("vp_001" in e for e in errors)

    # two unlinked pairs must not trip the cardinality check either
    draft["pairs"].append({"id": "vp_002", "concept": "", "bbox": [400, 10, 700, 90]})
    response = client.put(f"/api/studio/{country}/draft", json=draft)
    assert response.status_code == 200
    assert response.json()["errors"]


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


def test_template_upload_deskews_and_keeps_the_original(studio_country):
    """A 3° skew must come out straight — every block drawn on a skewed
    template inherits the skew."""
    import cv2
    import numpy as np

    card = np.full((500, 900, 3), 235, dtype=np.uint8)
    cv2.putText(card, "FICHA CLINICA - TEST CARD", (40, 250),
                cv2.FONT_HERSHEY_SIMPLEX, 2.2, (20, 20, 20), 6, cv2.LINE_AA)
    matrix = cv2.getRotationMatrix2D((450, 250), 3.0, 1.0)
    skewed = cv2.warpAffine(card, matrix, (900, 500), borderValue=(235, 235, 235))
    ok, encoded = cv2.imencode(".jpg", skewed)
    assert ok

    response = client.post(
        f"/api/studio/{studio_country}/template",
        files={"file": ("card.jpg", encoded.tobytes(), "image/jpeg")},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["cleaning"]["deskew_applied"] != 0
    assert abs(body["cleaning"]["deskew_applied"]) <= 10

    saved = cv2.imread(str(settings.COUNTRIES_DIR / studio_country / "template.jpg"))
    original = cv2.imread(str(settings.COUNTRIES_DIR / studio_country / "template_original.jpg"))
    assert saved is not None and original is not None
    # the straightened mask is horizontal again: min-area angle near 0 or 90
    gray = cv2.cvtColor(saved, cv2.COLOR_BGR2GRAY)
    coords = cv2.findNonZero(cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)[1])
    angle = cv2.minAreaRect(coords)[-1]
    angle = -(90 + angle) if angle < -45 else -angle
    assert abs(angle) < 1.0, f"template still skewed by {angle:.2f} degrees"
