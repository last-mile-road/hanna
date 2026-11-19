import sys
from pathlib import Path

import pandas as pd
import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import location_resolver  # noqa: E402

GAZ = ROOT / "countries/mozambique/gazetteer"


def test_admin_counts_match_cod_ab_v02():
    admin = pd.read_csv(GAZ / "admin_units.csv")
    counts = admin["level"].value_counts().to_dict()
    assert counts[1] == 11
    assert counts[2] == 161
    assert counts[3] == 457
    assert counts[4] == 1712


def test_district_names_are_unique_so_province_can_be_inferred():
    admin = pd.read_csv(GAZ / "admin_units.csv")
    districts = admin[admin["level"] == 2]
    assert districts["name"].nunique() == len(districts) == 161


def test_location_contract():
    config = yaml.safe_load((ROOT / "countries/mozambique/location.yaml").read_text())
    assert config["province"]["on_card"] is False
    assert config["province"]["infer_from"] == "district"
    assert "administrative_post" in config["levels"]
    assert "facility" in config["levels"]
    assert set(config["match"]["never_fuzzy"]) == {
        "phone_number", "code_number", "gender", "person_name"
    }


def test_portuguese_key_keeps_maria_but_normalises_accents():
    assert location_resolver.key("Chiúre") == location_resolver.key("Chiure")
    assert location_resolver.key("Maria") != location_resolver.key("Marie")
    assert location_resolver.key("Localidade de Namogelia") == "namogelia"


def test_bilibiza_resolves_through_district_and_post():
    candidates = location_resolver.resolve("Chiure", "Namogelia", "Bilibiza")
    pcodes = {c["pcode"] for c in candidates}
    assert "MZ0204" in pcodes          # Chiúre district
    assert "MZ020405" in pcodes        # Namogelia administrative post
    assert "US-MOZ-00192" in pcodes    # Bilibiza facility


def test_combination_anchors_on_a_strong_district():
    import location_combinations as lc

    assert lc.best_district("Chiure", "mozambique")[0] == "Chiúre"
    assert lc.best_district("Chiave", "mozambique") is None  # 66.7, below the anchor threshold
    top = lc.rank("Mocuba", "Zambezia", "Muinamba", "mozambique")[0]
    assert top[1].district == "Mocuba"


def test_combination_keeps_bilibiza_in_chiure():
    import location_combinations as lc

    top = lc.rank("Chiave", "Namgogotra", "Bilibiza", "mozambique")[0]
    assert top[1].district == "Chiúre"
    assert top[1].locality == "Bilibiza"
