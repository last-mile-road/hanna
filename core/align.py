"""Template alignment and block cropping.

Adapted from v5 `align_and_extract_groups.py` (138 lines), which is the one
preprocessing path that has always worked. The template and the coordinates are
arguments, not constants. SIFT + CLAHE + USAC_MAGSAC.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

MIN_MATCH_COUNT = 15
_cache: dict[str, dict] = {}


@dataclass
class Alignment:
    image: np.ndarray
    template: np.ndarray
    good_matches: int


def preprocess_image(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(gray)


def ocr_preprocess(image: np.ndarray) -> np.ndarray:
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY) if image.ndim == 3 else image
    thresholded = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 15, 7
    )
    return cv2.cvtColor(thresholded, cv2.COLOR_GRAY2BGR)


def _template_features(template_path: Path) -> dict:
    key = str(template_path)
    if key in _cache:
        return _cache[key]
    template = cv2.imread(str(template_path))
    if template is None:
        raise FileNotFoundError(f"template not found: {template_path}")
    enhanced = preprocess_image(template)
    sift = cv2.SIFT_create(nfeatures=5000)
    kp, desc = sift.detectAndCompute(enhanced, None)
    _cache[key] = {
        "color": template,
        "enhanced": enhanced,
        "kp": kp,
        "desc": desc,
        "sift": sift,
    }
    return _cache[key]


def align_card(image_path: Path, template_path: Path) -> Alignment:
    """Warp the card photo onto the template. Raises when alignment is not credible."""
    tpl = _template_features(template_path)
    form = cv2.imread(str(image_path))
    if form is None:
        raise FileNotFoundError(f"card image not found: {image_path}")

    sift = tpl["sift"]
    kp_form, desc_form = sift.detectAndCompute(preprocess_image(form), None)
    if desc_form is None:
        raise ValueError("no SIFT features found in the card image")

    bf = cv2.BFMatcher()
    matches = bf.knnMatch(tpl["desc"], desc_form, k=2)
    good = [m for m, n in matches if m.distance < 0.7 * n.distance]
    if len(good) <= MIN_MATCH_COUNT:
        raise ValueError(
            f"not enough alignment matches: {len(good)} (need > {MIN_MATCH_COUNT})"
        )

    src = np.float32([tpl["kp"][m.queryIdx].pt for m in good]).reshape(-1, 1, 2)
    dst = np.float32([kp_form[m.trainIdx].pt for m in good]).reshape(-1, 1, 2)
    homography, _ = cv2.findHomography(src, dst, cv2.USAC_MAGSAC, 5.0)
    if homography is None:
        raise ValueError("homography could not be computed")

    h, w = tpl["enhanced"].shape
    aligned = cv2.warpPerspective(form, np.linalg.inv(homography), (w, h))
    return Alignment(image=aligned, template=tpl["color"], good_matches=len(good))


def crop_block(image: np.ndarray, bbox: list[int] | None) -> np.ndarray:
    if bbox is None:
        return image
    x1, y1, x2, y2 = bbox
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise ValueError(f"empty crop for bbox {bbox}")
    return crop


def save_jpeg(image: np.ndarray, path: Path, quality: int = 92) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image, [cv2.IMWRITE_JPEG_QUALITY, quality]):
        raise OSError(f"could not write {path}")
