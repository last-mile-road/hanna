"""Country resources: validated uploads kept on disk, indexed in the database."""

from __future__ import annotations

import hashlib
import re
import uuid
from pathlib import Path

from sqlalchemy import delete, select

from . import settings
from .schema import country_assets

MAX_BYTES = 25 * 1024 * 1024
KINDS = {"template", "test_card", "location", "mapping", "document"}
ALLOWED_EXT = {
    ".jpg", ".jpeg", ".png", ".pdf", ".csv", ".xlsx", ".xls",
    ".yaml", ".yml", ".zip", ".txt", ".docx",
}


class AssetError(ValueError):
    pass


def _safe_name(name: str) -> tuple[str, str]:
    base = Path(name or "upload").name
    base = re.sub(r"[^A-Za-z0-9._ -]", "_", base).strip() or "upload"
    suffix = Path(base).suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise AssetError(f"file type not allowed: {suffix or 'none'}")
    return base[:200], suffix


def root() -> Path:
    return settings.UPLOAD_DIR / "assets"


def save(
    conn,
    *,
    country_id: int,
    country_code: str,
    kind: str,
    filename: str,
    content_type: str | None,
    data: bytes,
    uploaded_by=None,
) -> dict:
    if kind not in KINDS:
        raise AssetError("unknown kind")
    if not data:
        raise AssetError("empty file")
    if len(data) > MAX_BYTES:
        raise AssetError("file is larger than 25 MB")
    safe, suffix = _safe_name(filename)

    folder = root() / country_code
    folder.mkdir(parents=True, exist_ok=True)
    stored = folder / f"{uuid.uuid4().hex}{suffix}"
    stored.write_bytes(data)

    row = conn.execute(
        country_assets.insert()
        .values(
            country_id=country_id,
            kind=kind,
            filename=safe,
            content_type=(content_type or "")[:128] or None,
            size_bytes=len(data),
            sha256=hashlib.sha256(data).hexdigest(),
            path=str(stored),
            uploaded_by=uploaded_by,
        )
        .returning(
            country_assets.c.id,
            country_assets.c.kind,
            country_assets.c.filename,
            country_assets.c.size_bytes,
            country_assets.c.created_at,
        )
    ).mappings().one()
    return dict(row)


def list_for_country(conn, country_id: int) -> list[dict]:
    rows = conn.execute(
        select(
            country_assets.c.id,
            country_assets.c.kind,
            country_assets.c.filename,
            country_assets.c.content_type,
            country_assets.c.size_bytes,
            country_assets.c.created_at,
        )
        .where(country_assets.c.country_id == country_id)
        .order_by(country_assets.c.created_at.desc())
    ).mappings().all()
    return [dict(r) for r in rows]


def get(conn, asset_id, country_id: int) -> dict | None:
    row = conn.execute(
        select(country_assets)
        .where(
            country_assets.c.id == asset_id,
            country_assets.c.country_id == country_id,
        )
    ).mappings().first()
    return dict(row) if row else None


def remove(conn, asset_id, country_id: int) -> bool:
    row = get(conn, asset_id, country_id)
    if row is None:
        return False
    conn.execute(delete(country_assets).where(country_assets.c.id == asset_id))
    try:
        Path(row["path"]).unlink(missing_ok=True)
    except OSError:
        pass
    return True
