"""Authentication endpoints: login, logout, me.

Cookie sessions only. The cookie value is random; the database keeps its hash.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from core import auth, settings
from core.db import connect

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


def _client(request: Request) -> tuple[str | None, str | None]:
    ip = request.client.host if request.client else None
    return ip, request.headers.get("user-agent")


def optional_account(request: Request) -> dict | None:
    token = request.cookies.get(settings.SESSION_COOKIE)
    if not token:
        return None
    with connect() as conn:
        return auth.resolve_session(conn, token)


def require_account(account: dict | None = Depends(optional_account)) -> dict:
    if account is None:
        raise HTTPException(401, "not signed in")
    return account


def _public(account: dict) -> dict:
    return {
        "id": str(account["id"]),
        "email": account["email"],
        "display_name": account["display_name"],
        "memberships": [
            {"country_id": m.get("country_id"), "role": m["role"]}
            for m in account.get("memberships", [])
        ],
    }


@router.post("/login")
def login(payload: LoginIn, request: Request, response: Response):
    ip, ua = _client(request)
    email = payload.email.strip().lower()
    with connect() as conn:
        account = auth.account_by_email(conn, email)
        if account is None:
            auth.audit(conn, "login_failed", detail={"email": email, "reason": "unknown"})
            conn.commit()
            raise HTTPException(401, "email or password is wrong")
        if auth.is_locked(account):
            auth.audit(
                conn, "login_failed", account_id=account["id"],
                detail={"reason": "locked"},
            )
            conn.commit()
            raise HTTPException(429, "too many attempts; try again in 15 minutes")
        if not auth.verify_password(account["password_hash"], payload.password):
            auth.register_failure(conn, account)
            auth.audit(
                conn, "login_failed", account_id=account["id"],
                detail={"reason": "password"},
            )
            conn.commit()
            raise HTTPException(401, "email or password is wrong")
        if not account["is_active"]:
            auth.audit(
                conn, "login_failed", account_id=account["id"],
                detail={"reason": "inactive"},
            )
            conn.commit()
            raise HTTPException(403, "account is suspended")

        token = auth.create_session(conn, account["id"], ip=ip, user_agent=ua)
        auth.clear_failures(conn, account["id"])
        auth.audit(conn, "login", account_id=account["id"], detail={"ip": ip})
        conn.commit()
        account = auth.resolve_session(conn, token)

    response.set_cookie(
        settings.SESSION_COOKIE,
        token,
        max_age=settings.SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return {"account": _public(account)}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(settings.SESSION_COOKIE)
    with connect() as conn:
        account = auth.resolve_session(conn, token)
        auth.revoke_session(conn, token)
        if account:
            auth.audit(conn, "logout", account_id=account["id"])
        conn.commit()
    response.delete_cookie(settings.SESSION_COOKIE, path="/")
    return {"ok": True}


@router.get("/me")
def me(account: dict = Depends(require_account)):
    return {"account": _public(account)}
