"""Authentication endpoints: login, logout, me.

Cookie sessions only. The cookie value is random; the database keeps its hash.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel

from sqlalchemy import select

from core import auth, settings
from core.db import connect
from core.schema import countries

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: str
    password: str


class AcceptInviteIn(BaseModel):
    display_name: str
    password: str


class RegisterIn(BaseModel):
    country: str
    email: str
    password: str
    display_name: str
    role: str = "tester"
    phone: str | None = None
    province: str | None = None
    district: str | None = None
    facility: str | None = None
    staff_number: str | None = None


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
            {
                "country_id": m.get("country_id"),
                "country_code": m.get("country_code"),
                "role": m["role"],
                "status": m.get("status"),
                "province": m.get("province"),
                "district": m.get("district"),
                "facility": m.get("facility"),
            }
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


@router.post("/register")
def register(payload: RegisterIn, request: Request, response: Response):
    """Desktop accounts are invitation-only; field users register at /field/register."""
    if not settings.ALLOW_OPEN_REGISTRATION:
        raise HTTPException(403, "accounts are created by invitation")
    ip, ua = _client(request)
    role = payload.role.strip().lower()
    if role not in {"tester", "notifier"}:
        raise HTTPException(403, "this role cannot self-register")
    with connect() as conn:
        country = conn.execute(
            select(countries.c.id, countries.c.code, countries.c.name).where(
                countries.c.code == payload.country.strip().lower()
            )
        ).mappings().first()
        if country is None:
            raise HTTPException(404, "unknown country")
        if auth.account_by_email(conn, payload.email) is not None:
            raise HTTPException(409, "an account with this email already exists")
        try:
            row = auth.create_account(
                conn,
                email=payload.email,
                password=payload.password,
                display_name=payload.display_name,
                country_id=country["id"],
                role=role,
                phone=payload.phone,
                province=payload.province,
                district=payload.district,
                facility=payload.facility,
                staff_number=payload.staff_number,
                status="active",
            )
            token = auth.create_session(conn, row["id"], ip=ip, user_agent=ua)
            auth.audit(
                conn,
                "registered",
                account_id=row["id"],
                country_id=country["id"],
                detail={"role": role, "ip": ip},
            )
            conn.commit()
            account = auth.resolve_session(conn, token)
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(422, str(exc))
        except Exception:
            conn.rollback()
            raise

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


@router.post("/invite/{token}")
def accept_invite(token: str, payload: AcceptInviteIn, request: Request, response: Response):
    ip, ua = _client(request)
    with connect() as conn:
        try:
            row = auth.accept_invitation(
                conn,
                token,
                display_name=payload.display_name,
                password=payload.password,
            )
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(422, str(exc))
        session = auth.create_session(conn, row["id"], ip=ip, user_agent=ua)
        auth.audit(conn, "invitation_accepted", account_id=row["id"],
                   detail={"ip": ip})
        conn.commit()
        account = auth.resolve_session(conn, session)
    response.set_cookie(
        settings.SESSION_COOKIE,
        session,
        max_age=settings.SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )
    return {"account": _public(account)}
