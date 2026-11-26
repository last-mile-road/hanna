"""One message thread per country: the record of what was asked and answered."""

from __future__ import annotations

from sqlalchemy import select

from .schema import country_messages

MAX_LENGTH = 4000


class MessageError(ValueError):
    pass


def add(
    conn,
    *,
    country_id: int,
    author_name: str,
    body: str,
    account_id=None,
    field_user_id=None,
    context_type: str | None = None,
    context_id: str | None = None,
) -> dict:
    text = (body or "").strip()
    if not text:
        raise MessageError("a message cannot be empty")
    if len(text) > MAX_LENGTH:
        raise MessageError(f"a message is limited to {MAX_LENGTH} characters")
    row = conn.execute(
        country_messages.insert()
        .values(
            country_id=country_id,
            author_account_id=account_id,
            author_field_user_id=field_user_id,
            author_name=author_name.strip()[:128] or "Unknown",
            body=text,
            context_type=context_type,
            context_id=context_id,
        )
        .returning(
            country_messages.c.id,
            country_messages.c.author_name,
            country_messages.c.body,
            country_messages.c.created_at,
        )
    ).mappings().one()
    return dict(row)


def list_for_country(conn, country_id: int, limit: int = 200) -> list[dict]:
    rows = conn.execute(
        select(
            country_messages.c.id,
            country_messages.c.author_name,
            country_messages.c.body,
            country_messages.c.context_type,
            country_messages.c.context_id,
            country_messages.c.created_at,
        )
        .where(country_messages.c.country_id == country_id)
        .order_by(country_messages.c.created_at.desc())
        .limit(limit)
    ).mappings().all()
    return [dict(r) for r in rows]
