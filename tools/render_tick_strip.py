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
    left: int = 56,
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
    left: int = 56,
    pad: int = 12,
    font: float = 1.1,
    thickness: int = 3,
    separator: bool = True,
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
            if separator:
                # Two wide tiles stacked with nothing between them read as one
                # block: the mark in the upper tile got answered as the lower
                # band. A grey rule and a taller gap keep them separate.
                parts.append(np.full((2, width, 3), (170, 170, 170), np.uint8))
                parts.append(np.full((gap, width, 3), 255, np.uint8))
    return np.vstack(parts)


def render_rows(
    image: np.ndarray,
    rows: list[list[int]],
    left: int = 80,
    pad: int = 14,
    gap: int = 24,
    font: float = 1.1,
    thickness: int = 3,
    colour: tuple[int, int, int] = (30, 30, 30),
    start: int = 1,
    inset: int = 3,
    scale: float = 2.5,
    headers: tuple[str, str] | None = ("SIM", "NÃO"),
) -> np.ndarray:
    """Stack whole rows, crop inside the printed box, enlarge, number and space them.

    The printed box outline looks like a mark to a small VLM. Cropping a few pixels
    inside it leaves a plain white tile when the box is empty; a tick or cross still
    crosses the tile. Headers name the two columns.
    """
    pieces = []
    for n, (x1, y1, x2, y2) in enumerate(rows, start=start):
        crop = image[y1 + inset:y2 - inset, x1 + inset:x2 - inset].copy()
        if scale != 1.0:
            crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
        canvas = cv2.copyMakeBorder(crop, pad, pad, left, pad, cv2.BORDER_CONSTANT, value=WHITE)
        text = str(n)
        (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, font, thickness)
        cv2.putText(
            canvas,
            text,
            (max(2, (left - tw) // 2), pad + crop.shape[0] // 2 + th // 2),
            cv2.FONT_HERSHEY_SIMPLEX,
            font,
            colour,
            thickness,
            cv2.LINE_AA,
        )
        pieces.append(canvas)
    width = max(piece.shape[1] for piece in pieces)
    padded = [
        cv2.copyMakeBorder(piece, 0, 0, 0, width - piece.shape[1], cv2.BORDER_CONSTANT, value=WHITE)
        for piece in pieces
    ]
    parts = []
    for index, piece in enumerate(padded):
        parts.append(piece)
        if index < len(padded) - 1:
            parts.append(np.full((gap, width, 3), 255, np.uint8))
    body = np.vstack(parts)
    if headers:
        header = np.full((42, width, 3), 255, np.uint8)
        half = width // 2
        for label, centre in ((headers[0], half // 2), (headers[1], half + half // 2)):
            (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.7, 2)
            cv2.putText(
                header, label, (centre - tw // 2, 28),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7, colour, 2, cv2.LINE_AA,
            )
        body = np.vstack([header, body])
    return body
