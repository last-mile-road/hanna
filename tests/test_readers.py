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


def test_routing_sends_only_tables_to_mineru():
    routing = yaml.safe_load((COUNTRY / "routing-box.yaml").read_text())
    mineru_blocks = {b for b, p in routing["blocks"].items() if p == "mineru-standard"}
    assert mineru_blocks == {"def_exam", "nerve_assessment"}
    assert not (mineru_blocks & VISUAL_BLOCKS)


def test_reader_refuses_visual_blocks(tmp_path: Path):
    crop = tmp_path / "crop.jpg"
    crop.write_bytes(b"not read: the block check runs before any request")
    for block in ("hand_feet_assessment", "skin_patch", "general_demographic"):
        with pytest.raises(ValueError, match="no table reader"):
            readers.read_block(crop, block)


def test_supported_blocks_match_the_routing():
    assert readers.SUPPORTED_BLOCKS == {"def_exam", "nerve_assessment"}
