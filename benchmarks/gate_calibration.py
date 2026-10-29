"""Client-side gate calibration: which metrics move when a capture goes wrong?

Phase 0 measurement, 2026-10-29. No model calls: this measures the cheap 600 px checks a
phone can run before uploading, against deliberately damaged copies of the real cards.
It answers "does the gate see this?" — not "would the model have failed?". The second
question needs the resolution sweep and is reported with it.

    python benchmarks/gate_calibration.py
    python benchmarks/gate_calibration.py --forms form16,form21 --out output/gate_calibration
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import cv2
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core import settings  # noqa: E402

DEFAULT_FORMS = ["form16", "form19", "form21", "form22", "form23", "form25", "form30"]
THUMB_LONG_EDGE = 600


def thumbnail(image: np.ndarray) -> np.ndarray:
    height, width = image.shape[:2]
    scale = THUMB_LONG_EDGE / max(height, width)
    return cv2.resize(image, (round(width * scale), round(height * scale)),
                      interpolation=cv2.INTER_AREA)


def metrics(image: np.ndarray) -> dict:
    thumb = thumbnail(image)
    gray = cv2.cvtColor(thumb, cv2.COLOR_BGR2GRAY)
    laplacian = cv2.Laplacian(gray, cv2.CV_64F)
    saturated = np.all(thumb >= 250, axis=2)

    edges = cv2.Canny(gray, 60, 160)
    edges = cv2.dilate(edges, np.ones((3, 3), np.uint8), iterations=1)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    area_fraction = 0.0
    if contours:
        largest = max(contours, key=cv2.contourArea)
        hull = cv2.convexHull(largest)
        area_fraction = float(cv2.contourArea(hull)) / (thumb.shape[0] * thumb.shape[1])

    return {
        "brightness_mean": round(float(gray.mean()), 1),
        "brightness_std": round(float(gray.std()), 1),
        "blur_laplacian_var": round(float(laplacian.var()), 1),
        "glare_fraction": round(float(saturated.mean()), 4),
        "edge_area_fraction": round(area_fraction, 3),
    }


def defects(image: np.ndarray) -> dict[str, np.ndarray]:
    height, width = image.shape[:2]
    long_edge = max(height, width)

    blurred = cv2.GaussianBlur(image, (0, 0), sigmaX=0.0016 * long_edge)
    dark = np.clip(image.astype(np.float32) * 0.35, 0, 255).astype(np.uint8)
    bright = np.clip(image.astype(np.float32) + 90, 0, 255).astype(np.uint8)

    glare = image.copy()
    cv2.ellipse(
        glare,
        (int(width * 0.35), int(height * 0.3)),
        (int(width * 0.18), int(height * 0.12)),
        15, 0, 360, (255, 255, 255), -1,
    )

    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), 3.0, 1.0)
    rotated = cv2.warpAffine(image, matrix, (width, height), borderMode=cv2.BORDER_REPLICATE)

    clipped = image[:, int(width * 0.12):]

    return {
        "baseline": image,
        "blurred": blurred,
        "dark": dark,
        "overexposed": bright,
        "glare": glare,
        "rotated_3deg": rotated,
        "card_clipped_12pct": clipped,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--forms", default=",".join(DEFAULT_FORMS))
    ap.add_argument("--out", default=str(ROOT / "output" / "gate_calibration"))
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    forms = [f.strip() for f in args.forms.split(",") if f.strip()]
    rows: list[dict] = []
    for form in forms:
        path = settings.IMAGES_DIR / f"{form}.jpg"
        image = cv2.imread(str(path))
        if image is None:
            print(f"missing: {path}")
            continue
        for name, variant in defects(image).items():
            row = {"form": form, "variant": name, **metrics(variant)}
            rows.append(row)
            print(
                f"{form:8s} {name:18s} bright={row['brightness_mean']:6.1f} "
                f"blur={row['blur_laplacian_var']:8.1f} "
                f"glare={row['glare_fraction']:.4f} area={row['edge_area_fraction']:.3f}",
                flush=True,
            )

    (out / "results.json").write_text(json.dumps(rows, indent=2))

    variants = list(dict.fromkeys(r["variant"] for r in rows))
    print("\nmedian across forms")
    print(f"{'variant':20s} {'bright':>7s} {'blur':>9s} {'glare':>7s} {'area':>6s}")
    for variant in variants:
        subset = [r for r in rows if r["variant"] == variant]
        median = lambda key: float(np.median([r[key] for r in subset]))  # noqa: E731
        print(
            f"{variant:20s} {median('brightness_mean'):7.1f} "
            f"{median('blur_laplacian_var'):9.1f} {median('glare_fraction'):7.4f} "
            f"{median('edge_area_fraction'):6.3f}"
        )
    print(f"\nwritten: {out / 'results.json'}")


if __name__ == "__main__":
    main()
