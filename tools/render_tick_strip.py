"""Render a tick strip the way the VLM will see it.

The crop is the card region only: the boxes plus a small margin for alignment drift. The
white margin and the band numbers are added *after* cropping, so the crop never eats into
the printed words beside the column.

    strip = [x1, y1, x2, y2]            card pixels
    bands = [[x, y, w, h], ...]         each box, card pixels
    -> image with a white left margin, numbered bands, white border
"""

from __future__ import annotations

import cv2
import numpy as np

WHITE = (255, 255, 255)


def render_strip(
    image: np.ndarray,
    strip: list[int],
    bands: list[list[int]],
    left: int = 60,
    pad: int = 12,
    font: float = 1.1,
    thickness: int = 3,
    colour: tuple[int, int, int] = (30, 30, 30),
    start: int = 1,
) -> np.ndarray:
    """Crop, then pad white on the left and draw the band numbers into that margin."""
    x1, y1, x2, y2 = strip
    crop = image[y1:y2, x1:x2].copy()
    canvas = cv2.copyMakeBorder(crop, pad, pad, left, pad, cv2.BORDER_CONSTANT, value=WHITE)
    for n, (bx, by, bw, bh) in enumerate(bands, start=start):
        centre_y = pad + (by - y1) + bh // 2
        text = str(n)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font, thickness)
        cv2.putText(
            canvas,
            text,
            (max(2, (left - tw) // 2), centre_y + th // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            font,
            colour,
            thickness,
            cv2.LINE_AA,
        )
    return canvas


def stack_bands(
    image: np.ndarray,
    strip: list[int],
    bands: list[list[int]],
    gap: int = 60,
    left: int = 60,
    pad: int = 12,
    font: float = 1.1,
    thickness: int = 3,
) -> np.ndarray:
    """Vertical stack for a horizontal row (gender): one band per option, gap between."""
    x1, y1, x2, y2 = strip
    rendered = []
    for index, (bx, by, bw, bh) in enumerate(bands, start=1):
        band_strip = [bx, by, bx + bw, by + bh]
        piece = render_strip(image, band_strip, [[bx, by, bw, bh]], left=left, pad=pad,
                             font=font, thickness=thickness, start=index)
        rendered.append(piece)
    width = max(piece.shape[1] for piece in rendered)
    padded = []
    for piece in rendered:
        extra = width - piece.shape[1]
        padded.append(cv2.copyMakeBorder(piece, 0, 0, 0, extra, cv2.BORDER_CONSTANT, value=WHITE))
    parts = []
    for index, piece in enumerate(padded):
        parts.append(piece)
        if index < len(padded) - 1:
            parts.append(np.full((gap, width, 3), 255, np.uint8))
    return np.vstack(parts)
