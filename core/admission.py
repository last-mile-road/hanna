"""Page identity and the binary admission gate.

The gate runs before any inference: align each uploaded photo against every
registered page template, pick the strongest match, and admit only when each
side is unambiguous and the card edges are inside the frame. Good or retake;
there is no middle state, because a bad extraction costs more than a retake.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import cv2
import numpy as np
import yaml

from . import align, settings

# Calibrated on 2026-11-09 against the two Mozambique templates: a correct side
# gives 600+ inliers on a 1600 px phone-sized image and 4000+ on the template
# itself; the wrong side gives 9-11. The thresholds are deliberately conservative.
MIN_INLIERS = 40
MIN_MARGIN = 20
MIN_INLIER_RATIO = 0.5
MIN_AREA_RATIO = 0.03
MAX_AREA_RATIO = 8.0
EDGE_MARGIN = 0.01


@dataclass
class PageSpec:
    id: str
    page_no: int
    template: Path
    form_variant: str
    label: str = ""
    required: bool = True


@dataclass
class PageMatch:
    spec: PageSpec | None
    shot_no: int
    inliers: int = 0
    margin: int = 0
    rotation: int = 0
    area_ratio: float = 0.0
    reasons: list[str] = field(default_factory=list)
    quality: dict[str, Any] = field(default_factory=dict)

    @property
    def admitted(self) -> bool:
        return self.spec is not None and not self.reasons


@dataclass
class Assessment:
    pages: list[PageMatch]
    admitted: bool
    message: str
    pages_missing: list[str] = field(default_factory=list)
    duplicates: list[str] = field(default_factory=list)


def load_pages(country: str) -> list[PageSpec]:
    path = settings.COUNTRIES_DIR / country / "pages.yaml"
    if not path.exists():
        raise FileNotFoundError(f"page registry not found: {path}")
    data = yaml.safe_load(path.read_text()) or {}
    specs: list[PageSpec] = []
    for raw in data.get("pages", []):
        specs.append(
            PageSpec(
                id=raw["id"],
                page_no=int(raw["page_no"]),
                template=(settings.COUNTRIES_DIR / country / raw["template"]).resolve(),
                form_variant=raw.get("form_variant", raw["id"]),
                label=raw.get("label", ""),
                required=bool(raw.get("required", True)),
            )
        )
    if not specs:
        raise ValueError(f"page registry is empty: {path}")
    return specs


def _rotation(homography: np.ndarray) -> int:
    points = np.float32([[[0.0, 0.0]], [[100.0, 0.0]]])
    mapped = cv2.perspectiveTransform(points, homography).reshape(-1, 2)
    angle = float(
        np.degrees(np.arctan2(mapped[1][1] - mapped[0][1], mapped[1][0] - mapped[0][0]))
    )
    while angle <= -180:
        angle += 360
    while angle > 180:
        angle -= 360
    nearest = round(angle / 90) * 90
    return int(nearest) if abs(angle - nearest) <= 15 else int(round(angle))


def _geometry(alignment: align.Alignment, spec: PageSpec) -> tuple[float, bool, bool]:
    """Area ratio, convexity, and whether all four card corners are inside the photo."""
    template = cv2.imread(str(spec.template))
    if template is None or alignment.homography is None:
        return 0.0, False, False
    height, width = template.shape[:2]
    corners = np.float32(
        [[0, 0], [width, 0], [width, height], [0, height]]
    ).reshape(-1, 1, 2)
    mapped = cv2.perspectiveTransform(corners, alignment.homography).reshape(-1, 2)
    area_ratio = float(cv2.contourArea(mapped.astype(np.float32)) / float(width * height))
    convex = bool(cv2.isContourConvex(mapped.astype(np.float32)))
    source_height, source_width = alignment.source_shape
    margin_x = source_width * EDGE_MARGIN
    margin_y = source_height * EDGE_MARGIN
    inside = bool(
        np.all(mapped[:, 0] >= -margin_x)
        and np.all(mapped[:, 0] <= source_width + margin_x)
        and np.all(mapped[:, 1] >= -margin_y)
        and np.all(mapped[:, 1] <= source_height + margin_y)
    )
    return area_ratio, convex, inside


def match_image(image_path: Path, specs: list[PageSpec], shot_no: int) -> PageMatch:
    """Align one photo against every registered side and return the best match."""
    candidates: list[tuple[PageSpec, align.Alignment]] = []
    for spec in specs:
        try:
            candidates.append((spec, align.align_card(image_path, spec.template)))
        except (ValueError, FileNotFoundError):
            continue

    if not candidates:
        return PageMatch(None, shot_no, reasons=["Nenhum lado do cartão foi reconhecido."])

    candidates.sort(key=lambda item: item[1].inliers, reverse=True)
    best_spec, best = candidates[0]
    second = candidates[1][1].inliers if len(candidates) > 1 else 0
    margin = best.inliers - second
    area_ratio, convex, inside = _geometry(best, best_spec)

    reasons: list[str] = []
    if best.inliers < MIN_INLIERS:
        reasons.append(f"Poucos pontos de alinhamento ({best.inliers}).")
    if margin < MIN_MARGIN:
        reasons.append("A fotografia parece ser de mais de um lado.")
    if best.good_matches and best.inliers / best.good_matches < MIN_INLIER_RATIO:
        reasons.append("O alinhamento não é estável.")
    if area_ratio < MIN_AREA_RATIO or area_ratio > MAX_AREA_RATIO or not convex:
        reasons.append("O cartão não está direito no enquadramento.")
    if not inside:
        reasons.append("A borda do cartão está cortada.")

    return PageMatch(
        spec=best_spec,
        shot_no=shot_no,
        inliers=best.inliers,
        margin=margin,
        rotation=_rotation(best.homography) if best.homography is not None else 0,
        area_ratio=round(area_ratio, 3),
        reasons=reasons,
    )


def assess(
    matches: list[PageMatch], specs: list[PageSpec], require_back: bool = True
) -> Assessment:
    """One photo per side, every required side present, every side admitted."""
    by_side: dict[str, list[PageMatch]] = {}
    for match in matches:
        if match.spec is not None:
            by_side.setdefault(match.spec.id, []).append(match)

    duplicates: list[str] = []
    kept: list[PageMatch] = []
    for side, side_matches in by_side.items():
        side_matches.sort(key=lambda item: item.inliers, reverse=True)
        kept.append(side_matches[0])
        if len(side_matches) > 1:
            duplicates.append(side)
            for extra in side_matches[1:]:
                extra.reasons.append(f"Segunda fotografia do lado {side}.")
                kept.append(extra)

    missing: list[str] = []
    for spec in specs:
        if spec.id in by_side:
            continue
        required = spec.required or (require_back and spec.id == "back")
        if required:
            missing.append(spec.id)
            kept.append(
                PageMatch(spec=spec, shot_no=0, reasons=[f"Falta o lado {spec.id}."])
            )

    for match in matches:
        if match.spec is None:
            kept.append(match)

    kept.sort(key=lambda item: (item.spec.page_no if item.spec else 99, item.shot_no))
    admitted = not duplicates and not missing and all(item.admitted for item in kept)

    problems = []
    if missing:
        problems.append("falta o lado " + ", ".join(missing))
    if duplicates:
        problems.append("há duas fotografias do lado " + ", ".join(duplicates))
    if admitted:
        message = "Captura aceite."
    elif problems:
        message = "Capture de novo: " + "; ".join(problems) + "."
    else:
        message = "Repita a fotografia do lado assinalado."
    return Assessment(
        pages=kept,
        admitted=admitted,
        message=message,
        pages_missing=missing,
        duplicates=duplicates,
    )


def log_attempt(
    country: str, assessment: Assessment, path: Path | None = None
) -> None:
    """Append gate measurements for calibration. No patient data, no images."""
    path = path or (settings.OUTPUT_DIR / "admission.jsonl")
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {
        "at": datetime.now(timezone.utc).isoformat(),
        "country": country,
        "admitted": assessment.admitted,
        "missing": assessment.pages_missing,
        "duplicates": assessment.duplicates,
        "pages": [
            {
                "side": match.spec.id if match.spec else None,
                "shot_no": match.shot_no,
                "inliers": match.inliers,
                "margin": match.margin,
                "rotation": match.rotation,
                "area_ratio": match.area_ratio,
                "admitted": match.admitted,
                "reasons": match.reasons,
            }
            for match in assessment.pages
        ],
    }
    with path.open("a") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
