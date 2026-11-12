"""Reads of marks the clinic writes on the card, outside the printed fields.

The HANNA number is written in the blank top margin of the front after the
first capture. It is the key to the case on every later capture, so it has to
be read from the paper: typing it in is the step that gets forgotten.

Same strategy as the tick boxes: crop the area, add white space, make the
answer one easy choice, let the VLM read it. No ink decision, no template
difference.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from . import settings
from .caseid import is_valid

WHITE = (255, 255, 255)


def load_spec(country: str, key: str = "hanna_number") -> dict[str, Any] | None:
    path = settings.COUNTRIES_DIR / country / "identity.yaml"
    if not path.exists():
        return None
    data = yaml.safe_load(path.read_text()) or {}
    spec = data.get(key)
    return dict(spec) if spec else None


def prompt(spec: dict[str, Any]) -> str:
    return str(spec.get("prompt") or "").strip()


def digits(text: str | None) -> str | None:
    """Return the nine digits in an answer, or None when there are not nine.

    The model may answer JSON or bare digits. Anything that does not reduce to
    exactly nine digits is not a HANNA number.
    """
    if text is None:
        return None
    raw = str(text).strip()
    if not raw:
        return None
    try:
        parsed = json.loads(raw)
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        value = parsed.get("number", parsed.get("hanna_number"))
        if value is None:
            return None
        raw = str(value)
    found = re.sub(r"\D", "", raw)
    return found if len(found) == 9 else None


def render(aligned: np.ndarray, spec: dict[str, Any]) -> np.ndarray:
    x, y, w, h = [int(v) for v in spec["strip"]]
    crop = aligned[y : y + h, x : x + w].copy()
    pad = int(spec.get("pad", 20))
    crop = cv2.copyMakeBorder(crop, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=WHITE)
    scale = float(spec.get("upscale", 1.5))
    if scale != 1.0:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)
    return crop


def read_number(
    aligned: np.ndarray,
    country: str,
    page: str,
    client,
    profile: dict[str, Any],
    run_dir: Path,
    key: str = "hanna_number",
) -> dict[str, Any] | None:
    """Read the handwritten HANNA number from the page it is written on."""
    spec = load_spec(country, key)
    side = "front" if page in {"front", "draft"} else page
    if not spec or spec.get("page") != side:
        return None
    text = prompt(spec)
    image = render(aligned, spec)
    path = run_dir / f"{key}.jpg"
    cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
    completion = client.complete(text, [path], profile["model"], profile["settings"])
    value = digits(completion.text)
    return {
        "value": value,
        "valid": bool(value and is_valid(value)),
        "raw": completion.text,
        "path": path,
        "prompt": text,
        "latency_ms": completion.latency_ms,
        "input_tokens": completion.input_tokens or 0,
        "output_tokens": completion.output_tokens or 0,
        "cost_usd": completion.cost_usd or 0.0,
    }
