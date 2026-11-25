"""The country workspace API: resources a team uploads for its own country."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from core import assets
from core.db import connect
from server.auth_routes import require_account

router = APIRouter(prefix="/portal/api", tags=["portal"])


def _membership(account: dict) -> dict:
    for membership in account.get("memberships", []):
        if membership.get("country_id"):
            return membership
    raise HTTPException(403, "this account is not attached to a country")


def _country_id(membership: dict) -> int:
    return int(membership["country_id"])


@router.get("/assets")
def list_assets(account: dict = Depends(require_account)):
    membership = _membership(account)
    with connect() as conn:
        rows = assets.list_for_country(conn, _country_id(membership))
    return {"assets": [_public(row) for row in rows]}


@router.post("/assets", status_code=201)
async def upload_asset(
    kind: str = Form(...),
    file: UploadFile = File(...),
    account: dict = Depends(require_account),
):
    membership = _membership(account)
    data = await file.read()
    try:
        with connect() as conn:
            row = assets.save(
                conn,
                country_id=_country_id(membership),
                country_code=membership["country_code"],
                kind=kind,
                filename=file.filename or "upload",
                content_type=file.content_type,
                data=data,
                uploaded_by=account["id"],
            )
            conn.commit()
    except assets.AssetError as exc:
        raise HTTPException(422, str(exc))
    return {"asset": _public(row)}


@router.get("/assets/{asset_id}/download")
def download_asset(asset_id: str, account: dict = Depends(require_account)):
    membership = _membership(account)
    with connect() as conn:
        row = assets.get(conn, asset_id, _country_id(membership))
    if row is None:
        raise HTTPException(404, "asset not found")
    path = Path(row["path"])
    if not path.exists():
        raise HTTPException(410, "file is no longer on the server")
    return FileResponse(
        path,
        filename=row["filename"],
        media_type=row["content_type"] or "application/octet-stream",
    )


@router.delete("/assets/{asset_id}")
def delete_asset(asset_id: str, account: dict = Depends(require_account)):
    membership = _membership(account)
    role = membership.get("role")
    if role not in {"country_admin", "contributor", "tester", "project_admin"}:
        raise HTTPException(403, "your role cannot delete resources")
    with connect() as conn:
        removed = assets.remove(conn, asset_id, _country_id(membership))
        conn.commit()
    if not removed:
        raise HTTPException(404, "asset not found")
    return {"ok": True}


def _public(row: dict) -> dict:
    return {
        "id": str(row["id"]),
        "kind": row["kind"],
        "filename": row["filename"],
        "size_bytes": row["size_bytes"],
        "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
    }
