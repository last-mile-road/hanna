"""Who is calling: a country account (desktop) or a field user (phone)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Request

from core import auth, field, settings
from core.db import connect


def current_identity(request: Request) -> dict | None:
    with connect() as conn:
        account_token = request.cookies.get(settings.SESSION_COOKIE)
        account = auth.resolve_session(conn, account_token)
        if account is not None:
            return {"type": "account", "account": account}
        field_token = request.cookies.get(settings.FIELD_SESSION_COOKIE)
        field_user = field.resolve(conn, field_token)
        if field_user is not None:
            return {"type": "field", "field_user": field_user}
    return None


def require_identity(identity: dict | None = Depends(current_identity)) -> dict | None:
    if identity is None and settings.AUTH_REQUIRED:
        raise HTTPException(401, "sign in first")
    return identity
