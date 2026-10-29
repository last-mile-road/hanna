"""Upload gate: format, size, decode and the resolution floor."""

from __future__ import annotations

import numpy as np
import cv2
import pytest

from core import settings
from server import uploads


def _jpeg(width: int, height: int) -> bytes:
    # Textured on purpose: a flat frame is rejected as blank, so the fixture draws a grid.
    image = np.full((height, width, 3), 200, dtype=np.uint8)
    for x in range(0, width, 40):
        image[:, x:x + 2] = 40
    for y in range(0, height, 40):
        image[y:y + 2, :] = 40
    ok, encoded = cv2.imencode(".jpg", image)
    assert ok
    return encoded.tobytes()


def test_rejects_unknown_extension():
    assert uploads.validate("card.pdf", "application/pdf", 10) is not None
    assert uploads.validate("card.jpg", "image/jpeg", 10) is None


def test_rejects_oversized_file(monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_BYTES", 1000)
    assert uploads.validate("card.jpg", "image/jpeg", 2000) is not None


def test_rejects_empty_file():
    assert uploads.validate("card.jpg", "image/jpeg", 0) is not None


def test_decode_rejects_non_image():
    with pytest.raises(ValueError):
        uploads.decode(b"not an image")


def test_quality_floor(monkeypatch):
    monkeypatch.setattr(settings, "MIN_UPLOAD_LONG_EDGE", 1200)
    small = uploads.decode(_jpeg(600, 400))
    big = uploads.decode(_jpeg(1600, 1200))
    assert uploads.check_quality(small) is not None
    assert uploads.check_quality(big) is None


def test_save_uses_generated_name(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "UPLOAD_DIR", tmp_path)
    path = uploads.save(b"data", ".jpg")
    assert path.parent == tmp_path
    assert path.name.endswith(".jpg")
    assert path.read_bytes() == b"data"
    assert "card" not in path.name


def test_blank_frame_is_rejected_with_an_actionable_message():
    """A white frame reached the pipeline seven times on the first phone test."""
    blank = uploads.decode(_jpeg(1600, 1200))
    flat = np.full_like(blank, 220)
    message = uploads.check_quality(flat)
    assert message and "em branco" in message
