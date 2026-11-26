"""The country workspace API: resources a team uploads for its own country."""

from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel

from core import assets, auth, mail, messages, settings
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


class InviteIn(BaseModel):
    email: str
    role: str = "contributor"


def _admin(membership: dict) -> None:
    if membership.get("role") not in {"country_admin", "project_admin"}:
        raise HTTPException(403, "only a country administrator can manage the team")


@router.get("/team")
def team(account: dict = Depends(require_account)):
    membership = _membership(account)
    _admin(membership)
    with connect() as conn:
        members = auth.team_for_country(conn, _country_id(membership))
        invitations = auth.list_invitations(conn, _country_id(membership))
    return {
        "members": [
            {
                "name": m["display_name"],
                "email": m["email"],
                "role": m["role"],
                "status": m["status"],
                "facility": m["facility"],
            }
            for m in members
        ],
        "invitations": [
            {
                "id": str(i["id"]),
                "email": i["email"],
                "role": i["role"],
                "expires_at": i["expires_at"].isoformat(),
            }
            for i in invitations
        ],
    }


@router.post("/invitations", status_code=201)
def invite(payload: InviteIn, request: Request, account: dict = Depends(require_account)):
    membership = _membership(account)
    _admin(membership)
    email = payload.email.strip().lower()
    if "@" not in email or len(email) < 5:
        raise HTTPException(422, "a valid email is required")
    with connect() as conn:
        try:
            token, row = auth.create_invitation(
                conn,
                email=email,
                country_id=_country_id(membership),
                role=payload.role,
                created_by=account["id"],
            )
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(422, str(exc))
        auth.audit(
            conn,
            "invitation_created",
            account_id=account["id"],
            country_id=_country_id(membership),
            object_type="invitation",
            object_id=str(row["id"]),
            detail={"email": email, "role": payload.role},
        )
        conn.commit()
    link = str(request.base_url).rstrip("/") + f"/invite/{token}"
    sent = mail.send_mail(
        email,
        "Your HANNA country workspace invitation",
        f"<p>You have been invited to the HANNA workspace.</p>"
        f"<p><a href=\"{link}\">Set your password and sign in</a></p>"
        f"<p>The link is valid for {7} days.</p>",
        to_name="",
    )
    return {"invitation": {"id": str(row["id"]), "email": email, "role": payload.role},
            "link": link, "email_sent": sent}


@router.delete("/invitations/{invitation_id}")
def revoke(invitation_id: str, account: dict = Depends(require_account)):
    membership = _membership(account)
    _admin(membership)
    with connect() as conn:
        ok = auth.revoke_invitation(conn, invitation_id, _country_id(membership))
        conn.commit()
    if not ok:
        raise HTTPException(404, "invitation not found")
    return {"ok": True}


class MessageIn(BaseModel):
    body: str
    context_type: str | None = None
    context_id: str | None = None


@router.get("/messages")
def list_messages(account: dict = Depends(require_account)):
    membership = _membership(account)
    with connect() as conn:
        rows = messages.list_for_country(conn, _country_id(membership))
    return {
        "messages": [
            {
                "id": str(m["id"]),
                "author": m["author_name"],
                "body": m["body"],
                "context_type": m["context_type"],
                "context_id": m["context_id"],
                "created_at": m["created_at"].isoformat(),
            }
            for m in rows
        ]
    }


@router.post("/messages", status_code=201)
def post_message(payload: MessageIn, account: dict = Depends(require_account)):
    membership = _membership(account)
    with connect() as conn:
        try:
            row = messages.add(
                conn,
                country_id=_country_id(membership),
                author_name=account["display_name"],
                body=payload.body,
                account_id=account["id"],
                context_type=payload.context_type,
                context_id=payload.context_id,
            )
        except messages.MessageError as exc:
            conn.rollback()
            raise HTTPException(422, str(exc))
        auth.audit(
            conn,
            "message_posted",
            account_id=account["id"],
            country_id=_country_id(membership),
            object_type="message",
            object_id=str(row["id"]),
        )
        conn.commit()
    if settings.NOTIFY_EMAIL:
        mail.send_mail(
            settings.NOTIFY_EMAIL,
            f"HANNA message from {account['display_name']}",
            f"<p><strong>{account['display_name']}</strong> wrote in the "
            f"{membership.get('country_code')} workspace:</p><p>{payload.body}</p>",
        )
    return {"message": {
        "id": str(row["id"]),
        "author": row["author_name"],
        "body": row["body"],
        "created_at": row["created_at"].isoformat(),
    }}
