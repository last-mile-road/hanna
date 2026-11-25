"""The official portal's API: field registration and MoH-ID sign-in."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel
from sqlalchemy import select

from core import field, settings
from core.db import connect
from core.schema import countries

router = APIRouter(prefix="/field", tags=["field"])


class RegisterIn(BaseModel):
    country: str
    moh_id: str
    display_name: str
    phone: str | None = None
    province: str | None = None
    district: str | None = None
    facility: str | None = None
    role: str = "clinician"


class LoginIn(BaseModel):
    country: str
    moh_id: str


def _country(conn, code: str):
    return conn.execute(
        select(countries.c.id, countries.c.code, countries.c.name).where(
            countries.c.code == code.strip().lower()
        )
    ).mappings().first()


def _client(request: Request) -> tuple[str | None, str | None]:
    return (
        request.client.host if request.client else None,
        request.headers.get("user-agent"),
    )


def _public(user: dict) -> dict:
    return {
        k: (str(v) if k == "id" else v)
        for k, v in user.items()
    }


def _set_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        settings.FIELD_SESSION_COOKIE,
        token,
        max_age=settings.FIELD_SESSION_TTL_DAYS * 86400,
        httponly=True,
        secure=settings.COOKIE_SECURE,
        samesite="lax",
        path="/",
    )


@router.post("/register")
def register(payload: RegisterIn, request: Request, response: Response):
    ip, ua = _client(request)
    with connect() as conn:
        country = _country(conn, payload.country)
        if country is None:
            raise HTTPException(404, "unknown country")
        try:
            user = field.register(
                conn,
                country_id=country["id"],
                moh_id=payload.moh_id,
                display_name=payload.display_name,
                phone=payload.phone,
                province=payload.province,
                district=payload.district,
                facility=payload.facility,
                role=payload.role.strip().lower(),
                status="active",
            )
        except ValueError as exc:
            conn.rollback()
            raise HTTPException(409, str(exc))
        token = field.start_session(conn, user["id"], ip=ip, user_agent=ua)
        field.audit_login(
            conn, "field_registered", field_user_id=user["id"],
            country_id=country["id"], detail={"ip": ip},
        )
        conn.commit()
    _set_cookie(response, token)
    return {"field_user": _public(user)}


@router.post("/login")
def login(payload: LoginIn, request: Request, response: Response):
    ip, ua = _client(request)
    with connect() as conn:
        country = _country(conn, payload.country)
        if country is None:
            raise HTTPException(404, "unknown country")
        user = field.by_moh(conn, country["id"], payload.moh_id)
        if user is None:
            field.audit_login(
                conn, "field_login_failed", country_id=country["id"],
                detail={"moh_id": payload.moh_id.strip(), "reason": "unknown"},
            )
            conn.commit()
            raise HTTPException(401, "this MoH ID is not registered in this country")
        if user["status"] != "active":
            field.audit_login(
                conn, "field_login_failed", field_user_id=user["id"],
                country_id=country["id"], detail={"reason": user["status"]},
            )
            conn.commit()
            raise HTTPException(403, "registration is not active")
        token = field.start_session(conn, user["id"], ip=ip, user_agent=ua)
        field.audit_login(
            conn, "field_login", field_user_id=user["id"],
            country_id=country["id"], detail={"ip": ip},
        )
        conn.commit()
    _set_cookie(response, token)
    return {"field_user": _public(field_view(user))}


def field_view(user: dict) -> dict:
    return {
        "id": user["id"],
        "moh_id": user["moh_id"],
        "display_name": user["display_name"],
        "role": user["role"],
        "facility": user.get("facility"),
        "status": user["status"],
    }


@router.get("/me")
def me(request: Request):
    token = request.cookies.get(settings.FIELD_SESSION_COOKIE)
    with connect() as conn:
        user = field.resolve(conn, token)
    if user is None:
        raise HTTPException(401, "not signed in")
    return {"field_user": _public(field_view(user))}


@router.post("/logout")
def logout(request: Request, response: Response):
    token = request.cookies.get(settings.FIELD_SESSION_COOKIE)
    with connect() as conn:
        field.revoke(conn, token)
        conn.commit()
    response.delete_cookie(settings.FIELD_SESSION_COOKIE, path="/")
    return {"ok": True}
