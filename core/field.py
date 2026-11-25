"""Field users and their sessions — the official portal on a phone.

A field user is known by their MoH ID within a country. There is no password:
the test server and the first production cut both accept the ID alone, with the
country's registration check and a second factor as later additions.
"""

from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select, update

from . import settings
from .auth import audit, new_token, now, token_hash
from .schema import field_sessions, field_users


def by_moh(conn, country_id: int, moh_id: str) -> dict | None:
    row = conn.execute(
        select(field_users).where(
            field_users.c.country_id == country_id,
            field_users.c.moh_id == moh_id.strip(),
        )
    ).mappings().first()
    return dict(row) if row else None


def register(
    conn,
    *,
    country_id: int,
    moh_id: str,
    display_name: str,
    phone: str | None = None,
    province: str | None = None,
    district: str | None = None,
    facility: str | None = None,
    role: str = "clinician",
    status: str = "active",
) -> dict:
    moh_id = moh_id.strip()
    if not moh_id:
        raise ValueError("MoH ID is required")
    if role not in {"clinician", "supervisor"}:
        raise ValueError("unknown field role")
    if by_moh(conn, country_id, moh_id) is not None:
        raise ValueError("this MoH ID is already registered in this country")
    row = conn.execute(
        field_users.insert()
        .values(
            country_id=country_id,
            moh_id=moh_id,
            display_name=display_name.strip(),
            phone=phone or None,
            province=province or None,
            district=district or None,
            facility=facility or None,
            role=role,
            status=status,
        )
        .returning(
            field_users.c.id,
            field_users.c.moh_id,
            field_users.c.display_name,
            field_users.c.role,
            field_users.c.status,
            field_users.c.facility,
        )
    ).mappings().one()
    return dict(row)


def start_session(conn, field_user_id, *, ip: str | None = None, user_agent: str | None = None) -> str:
    token = new_token()
    conn.execute(
        field_sessions.insert().values(
            id=token_hash(token),
            field_user_id=field_user_id,
            expires_at=now() + timedelta(days=settings.FIELD_SESSION_TTL_DAYS),
            ip=ip,
            user_agent=(user_agent or "")[:500],
        )
    )
    conn.execute(
        update(field_users)
        .where(field_users.c.id == field_user_id)
        .values(last_login_at=now())
    )
    return token


def resolve(conn, token: str | None) -> dict | None:
    if not token:
        return None
    row = conn.execute(
        select(
            field_users.c.id,
            field_users.c.moh_id,
            field_users.c.display_name,
            field_users.c.role,
            field_users.c.status,
            field_users.c.facility,
            field_users.c.country_id,
        )
        .join(field_sessions, field_sessions.c.field_user_id == field_users.c.id)
        .where(
            field_sessions.c.id == token_hash(token),
            field_sessions.c.expires_at > now(),
            field_users.c.status == "active",
        )
    ).mappings().first()
    return dict(row) if row else None


def revoke(conn, token: str | None) -> None:
    if token:
        conn.execute(delete(field_sessions).where(field_sessions.c.id == token_hash(token)))


def audit_login(conn, action: str, *, field_user_id=None, country_id=None, detail=None) -> None:
    audit(
        conn,
        action,
        account_id=None,
        country_id=country_id,
        object_type="field_user",
        object_id=str(field_user_id) if field_user_id else None,
        detail=detail,
    )
