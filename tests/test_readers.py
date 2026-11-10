"""The table reader must never claim a block it cannot read.

The owner's rule: hand/feet and skin patches are visual annotation and stay with the
9B. This pins both halves of that — the routing file, and the reader's own refusal —
so a later edit to routing cannot quietly send a diagram to MinerU.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from core import readers, settings

COUNTRY = settings.COUNTRIES_DIR / "mozambique"
VISUAL_BLOCKS = {"hand_feet_assessment", "skin_patch", "visit1_sensory", "visit2_sensory",
                 "visit3_sensory", "visit4_sensory"}
TABLE_BLOCKS = {
    "def_exam", "nerve_assessment",
    "visit1_nerve", "visit2_nerve", "visit3_nerve", "visit4_nerve",
    "visit1_def_exam", "visit2_def_exam", "visit3_def_exam", "visit4_def_exam",
}


def test_routing_sends_only_tables_to_mineru():
    routing = yaml.safe_load((COUNTRY / "routing.yaml").read_text())
    mineru_blocks = {b for b, p in routing["blocks"].items() if p == "mineru-standard"}
    assert mineru_blocks == TABLE_BLOCKS
    assert not (mineru_blocks & VISUAL_BLOCKS)


def test_reader_refuses_visual_blocks(tmp_path: Path):
    crop = tmp_path / "crop.jpg"
    crop.write_bytes(b"not read: the block check runs before any request")
    for block in ("hand_feet_assessment", "skin_patch", "general_demographic"):
        with pytest.raises(ValueError, match="no table reader"):
            readers.read_block(crop, block)


def test_supported_blocks_match_the_routing():
    assert readers.SUPPORTED_BLOCKS == TABLE_BLOCKS


def test_treatment_is_routed_to_the_model_not_the_reader():
    """MinerU sees the grid's structure at advanced but not its handwriting."""
    routing = yaml.safe_load((COUNTRY / "routing.yaml").read_text())
    assert routing["blocks"].get("treatment_follow") is None
    with pytest.raises(ValueError, match="no table reader"):
        readers.read_block(Path("/dev/null"), "treatment_follow")


def test_treatment_grid_maps_months_to_doses_and_misses():
    """The real form16 advanced-tier shape: two printed rows per year, `20.25` for 2025."""
    months = "".join(f"<td>{name}</td>" for name in
                     ("Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho", "Julho",
                      "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro"))
    blanks = "<td></td>"
    markdown = (
        "<table>"
        '<tr><td rowspan="2">ANO</td><td rowspan="2"></td>'
        '<td colspan="12">Doses mensais supervisados</td><td rowspan="2">dosesmeses</td></tr>'
        f"<tr>{months}</tr>"
        '<tr><td rowspan="2">20.25</td><td>Dia</td>'
        f"{blanks * 4}<td>31/5Sa2</td>{blanks * 7}</tr>"
        f'<tr><td>Rubrica/Faltou</td>{blanks * 12}</tr>'
        "</table>"
    )
    values = readers.READERS["treatment_follow"](markdown)
    grid = yaml.safe_load(values["treatment_grid"])
    row = grid["rows"][0]
    assert row["year"] == 2025
    assert row["doses"] == [{"month": 5, "day": 31}]
    assert row["missed_months"] == []


def test_back_reader_maps_front_pairs_to_visit_pairs():
    raw = {
        "right_eye_blinking": "+",
        "ehf_right_eye": "2",
        "right_hipothenar": "N",
    }
    assert readers._back_values(raw, 1, readers.BACK_DEF_MAP) == {
        "v1_eye_blinking_right": "+",
        "v1_disability_grade_eye_right": "2",
        "v1_finger_abduction_right": "N",
    }
    assert readers._back_values(
        {"nerve_enlargement_cubital_right": "+"}, 2, {}
    ) == {"v2_nerve_enlargement_cubital_right": "+"}