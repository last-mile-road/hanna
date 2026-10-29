"""Database engine and connection helper."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.engine import Connection, Engine

from . import settings

_engine: Engine | None = None


def engine() -> Engine:
    global _engine
    if _engine is None:
        _engine = create_engine(settings.DATABASE_URL, pool_pre_ping=True)
    return _engine


def connect() -> Connection:
    return engine().connect()
