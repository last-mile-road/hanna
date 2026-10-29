"""Stream shaping: which blocks are new, and the SSE framing."""

from __future__ import annotations

from server.app import _sse
from server.events import new_blocks


def test_new_blocks_are_emitted_once():
    seen: set[str] = set()
    rows = [{"block_id": "a"}, {"block_id": "b"}]
    assert [b["block_id"] for b in new_blocks(rows, seen)] == ["a", "b"]
    assert new_blocks(rows, seen) == []

    rows.append({"block_id": "c"})
    assert [b["block_id"] for b in new_blocks(rows, seen)] == ["c"]
    assert seen == {"a", "b", "c"}


def test_sse_framing_is_named_and_json():
    frame = _sse("block", {"block_id": "nerve", "values": {"x": None}})
    assert frame.startswith("event: block\n")
    assert frame.endswith("\n\n")
    assert '"block_id": "nerve"' in frame
