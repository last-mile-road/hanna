"""Accounts, passwords and server-side sessions.

The password is argon2id. The cookie carries a random 32-byte value; the database
stores only its sha256, so a database leak does not hand anyone a live session.
Every login, failed login, logout and invitation is written to `audit_log`.
"""

from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select, update

from . import settings
from .schema import accounts, audit_log, memberships, sessions

_hasher = PasswordHasher()

MIN_PASSWORD_LENGTH = 12
MAX_FAILED_ATTEMPTS = 5
LOCK_MINUTES = 15


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored: str, password: str) -> bool:
    try:
        return _hasher.verify(stored, password)
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def new_token() -> str:
    return secrets.token_urlsafe(32)


def now() -> datetime:
    return datetime.now(timezone.utc)


def audit(
    conn,
    action: str,
    *,
    account_id=None,
    country_id=None,
    object_type: str | None = None,
    object_id: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        audit_log.insert().values(
            account_id=account_id,
            action=action,
            country_id=country_id,
            object_type=object_type,
            object_id=object_id,
            detail=detail,
        )
    )


def account_by_email(conn, email: str) -> dict | None:
    row = conn.execute(
        select(accounts).where(accounts.c.email == email.strip().lower())
    ).mappings().first()
    return dict(row) if row else None


def memberships_for(conn, account_id) -> list[dict]:
    rows = conn.execute(
        select(memberships,)
        .where(memberships.c.account_id == account_id)
    ).mappings().all()
    return [dict(r) for r in rows]


def create_account(
    conn,
    *,
    email: str,
    password: str,
    display_name: str,
    country_id: int | None,
    role: str,
) -> dict:
    if len(password) < MIN_PASSWORD_LENGTH:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LENGTH} characters")
    if role not in {"project_admin", "country_admin", "contributor", "reviewer", "viewer"}:
        raise ValueError("unknown role")
    if role == "project_admin" and country_id is not None:
        raise ValueError("project_admin is not scoped to a country")
    if role != "project_admin" and country_id is None:
        raise ValueError("a country role needs a country")
    row = conn.execute(
        accounts.insert()
        .values(
            email=email.strip().lower(),
            password_hash=hash_password(password),
            display_name=display_name.strip(),
        )
        .returning(accounts.c.id, accounts.c.email, accounts.c.display_name)
    ).mappings().one()
    conn.execute(
        memberships.insert().values(
            account_id=row["id"], country_id=country_id, role=role
        )
    )
    return dict(row)


def create_session(
    conn, account_id, *, ip: str | None = None, user_agent: str | None = None
) -> str:
    token = new_token()
    conn.execute(
        sessions.insert().values(
            id=token_hash(token),
            account_id=account_id,
            expires_at=now() + timedelta(days=settings.SESSION_TTL_DAYS),
            ip=ip,
            user_agent=(user_agent or "")[:500],
        )
    )
    return token


def resolve_session(conn, token: str | None) -> dict | None:
    """The account behind a cookie value, if the session is live."""
    if not token:
        return None
    row = conn.execute(
        select(
            accounts.c.id,
            accounts.c.email,
            accounts.c.display_name,
            accounts.c.is_active,
        )
        .join(sessions, sessions.c.account_id == accounts.c.id)
        .where(
            sessions.c.id == token_hash(token),
            sessions.c.expires_at > now(),
            accounts.c.is_active.is_(True),
        )
    ).mappings().first()
    if not row:
        return None
    account = dict(row)
    account["memberships"] = memberships_for(conn, account["id"])
    return account


def touch_session(conn, token: str) -> None:
    conn.execute(
        update(sessions)
        .where(sessions.c.id == token_hash(token))
        .values(
            last_seen_at=now(),
            expires_at=now() + timedelta(days=settings.SESSION_TTL_DAYS),
        )
    )


def revoke_session(conn, token: str | None) -> None:
    if token:
        conn.execute(delete(sessions).where(sessions.c.id == token_hash(token)))


def revoke_all_sessions(conn, account_id) -> None:
    conn.execute(delete(sessions).where(sessions.c.account_id == account_id))


def register_failure(conn, account: dict) -> None:
    attempts = int(account.get("failed_attempts") or 0) + 1
    values: dict[str, Any] = {"failed_attempts": attempts}
    if attempts >= MAX_FAILED_ATTEMPTS:
        values["locked_until"] = now() + timedelta(minutes=LOCK_MINUTES)
        values["failed_attempts"] = 0
    conn.execute(update(accounts).where(accounts.c.id == account["id"]).values(**values))


def clear_failures(conn, account_id) -> None:
    conn.execute(
        update(accounts)
        .where(accounts.c.id == account_id)
        .values(failed_attempts=0, locked_until=None, last_login_at=now())
    )


def is_locked(account: dict) -> bool:
    locked = account.get("locked_until")
    return bool(locked and locked > now())
