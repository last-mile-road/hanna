"""Compose the review map: a static PNG from OSM tiles, with the point and its uncertainty.

Why server-side: the coordinate belongs to a patient's village. Fetching tiles in the
browser sends it to a tile server from the phone; composing it here sends it once, from the
server, and the phone only receives a PNG. Tiles are cached on disk so a second view of the
same area costs nothing and a weak field connection still gets a picture.

Layout: the uncertainty ring is the point. A district centroid drawn as a bare pin claims a
precision the answer does not have, so the circle is sized from the match level and the map
is zoomed to fit it.

    from core.mapimage import compose
    path = compose(lat=-13.3778, lon=38.3703, radius_m=250, out=Path("map.png"))
"""

from __future__ import annotations

import io
import math
import urllib.request
from pathlib import Path

from PIL import Image, ImageDraw

TILE_URL = "https://tile.openstreetmap.org/{z}/{x}/{y}.png"
TILE_UA = "HANNA/0.1 (leprosy clinic card review; contact: last-mile-road)"
TILE_SIZE = 256
WIDTH, HEIGHT = 720, 380
RING_FRACTION = 0.34          # ring radius as a share of the shorter side
TILE_CACHE: Path | None = None


def _tile(z: int, x: int, y: int, cache: Path | None) -> Image.Image | None:
    if cache is not None:
        path = cache / str(z) / str(x) / f"{y}.png"
        if path.exists():
            try:
                return Image.open(path).convert("RGB")
            except OSError:
                pass
    request = urllib.request.Request(TILE_URL.format(z=z, x=x, y=y),
                                     headers={"User-Agent": TILE_UA})
    try:
        with urllib.request.urlopen(request, timeout=6) as response:
            data = response.read()
        if cache is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(data)
        return Image.open(io.BytesIO(data)).convert("RGB")
    except Exception:  # noqa: BLE001 - a missing tile must not fail the picture
        return None


def _zoom_for(lat: float, radius_m: float, pixels: int) -> int:
    """Pick the zoom at which the ring covers `pixels` across."""
    target = max(40.0, radius_m) * 2.4          # ring plus context
    for zoom in range(18, 2, -1):
        metres_per_pixel = 156543.03392 * math.cos(math.radians(lat)) / (2 ** zoom)
        if target / metres_per_pixel >= pixels:
            return zoom
    return 3


def _world_px(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    scale = TILE_SIZE * (2 ** zoom)
    x = (lon + 180.0) / 360.0 * scale
    siny = math.sin(math.radians(max(-85.05112878, min(85.05112878, lat))))
    y = (0.5 - math.log((1 + siny) / (1 - siny)) / (4 * math.pi)) * scale
    return x, y


def _fallback(lat: float, lon: float, radius_px: int) -> Image.Image:
    """No tiles: still show the ring and the point, honestly labelled."""
    image = Image.new("RGB", (WIDTH, HEIGHT), "#eef1ec")
    draw = ImageDraw.Draw(image)
    for step in range(0, WIDTH, 48):
        draw.line([(step, 0), (step, HEIGHT)], fill="#e3e7e0")
    for step in range(0, HEIGHT, 48):
        draw.line([(0, step), (WIDTH, step)], fill="#e3e7e0")
    return image


def compose(lat: float, lon: float, radius_m: float, out: Path,
            cache: Path | None = None, reviewed: bool = False) -> Path:
    """Write a PNG centred on the point.

    `reviewed`: a person moved the pin. The ring is a statement about the gazetteer's
    uncertainty, and it stops being true the moment a human corrects the point, so it is
    not drawn. The caller labels the pin as reviewer-placed instead.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.exists():
        return out

    ring_px = int(min(WIDTH, HEIGHT) * RING_FRACTION)
    zoom = _zoom_for(lat, radius_m, ring_px)
    centre_x, centre_y = _world_px(lat, lon, zoom)

    left = int(round(centre_x - WIDTH / 2))
    top = int(round(centre_y - HEIGHT / 2))
    first_tile = (left // TILE_SIZE, top // TILE_SIZE)
    last_tile = ((left + WIDTH) // TILE_SIZE, (top + HEIGHT) // TILE_SIZE)

    canvas = _fallback(lat, lon, ring_px)
    tiles = 0
    for tx in range(first_tile[0], last_tile[0] + 1):
        for ty in range(first_tile[1], last_tile[1] + 1):
            tile = _tile(zoom, tx % (2 ** zoom), ty, cache)
            if tile is None:
                continue
            tiles += 1
            canvas.paste(tile, (tx * TILE_SIZE - left, ty * TILE_SIZE - top))

    draw = ImageDraw.Draw(canvas, "RGBA")
    cx, cy = WIDTH / 2, HEIGHT / 2
    if not reviewed:
        draw.ellipse([cx - ring_px, cy - ring_px, cx + ring_px, cy + ring_px],
                     fill=(20, 83, 45, 28), outline=(20, 83, 45, 170), width=3)
    # Pin: a small teardrop above the point, the point itself at the centre.
    draw.ellipse([cx - 7, cy - 7, cx + 7, cy + 7], fill=(139, 26, 26, 255))
    draw.ellipse([cx - 3, cy - 3, cx + 3, cy + 3], fill=(255, 255, 255, 255))
    if not tiles:
        draw.text((12, HEIGHT - 22), "sem ligação — apenas a área aproximada",
                  fill=(95, 99, 104, 255))

    canvas.save(out, "PNG", optimize=True)
    return out
