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
VISUAL_BLOCKS = {"hand_feet_assessment", "skin_patch"}
TABLE_BLOCKS = {"def_exam", "nerve_assessment", "treatment_follow"}


def test_routing_sends_only_tables_to_mineru():
    routing = yaml.safe_load((COUNTRY / "routing-box.yaml").read_text())
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


def test_treatment_grid_maps_months_to_doses_and_misses():
    """The advanced-tier TMA table, trimmed to the shape MinerU returns."""
    markdown = (
        "<table>"
        "<tr><td>ANO</td><td>Janeiro</td><td>Fevereiro</td><td>Março</td><td>Abril</td>"
        "<td>Maio</td><td>Junho</td><td>Julho</td><td>Agosto</td><td>Setembro</td>"
        "<td>Outubro</td><td>Novembro</td><td>Dezembro</td></tr>"
        "<tr><td>2022</td><td></td><td>18</td><td>21</td><td></td><td></td><td></td>"
        "<td></td><td>F</td><td></td><td></td><td></td><td></td></tr>"
        "</table>"
    )
    values = readers.READERS["treatment_follow"](markdown)
    grid = yaml.safe_load(values["treatment_grid"])
    row = grid["rows"][0]
    assert row["year"] == 2022
    assert {"month": 2, "day": 18} in row["doses"]
    assert {"month": 3, "day": 21} in row["doses"]
    assert row["missed_months"] == [8]
