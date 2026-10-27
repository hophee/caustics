"""Database engine, sessions and the declarative base.

One ``AsyncSession`` per request or worker job. Services receive the session and never commit
themselves; the caller (request dependency or worker loop) commits once at the end, so every use
case is one transaction — e.g. an alert and its outbox message are saved together or not at all.
"""

import enum
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any, ClassVar

from sqlalchemy import ARRAY, Date, DateTime, Enum, MetaData, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Base for all tables; deterministic constraint names keep Alembic migrations stable.

    Type map: JSONB for dict/list-of-dict fields, ``text[]`` for list-of-str, timezone-aware
    timestamps, and Python enums stored as their string values (no native PG enums, so adding a
    value never needs a type migration).
    """

    metadata = MetaData(naming_convention=NAMING)
    type_annotation_map: ClassVar[dict[Any, Any]] = {
        dict[str, Any]: JSONB,
        dict[str, int]: JSONB,
        dict[str, object]: JSONB,
        list[dict[str, Any]]: JSONB,
        list[dict[str, str]]: JSONB,
        list[str]: ARRAY(Text),
        datetime: DateTime(timezone=True),
        date: Date,
        enum.Enum: Enum(enum.Enum, native_enum=False, length=40, values_callable=lambda e: [m.value for m in e]),
    }


class IdMixin:
    """UUID primary key, generated in Python so ids are known before flush."""

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """``created_at`` (UTC, timezone-aware). Always set from the injected clock's ``now`` by the
    service, never by a database default, so FixedClock tests are deterministic (D-073)."""

    created_at: Mapped[datetime]


def create_engine(url: str) -> AsyncEngine:
    """Async engine with sane pool settings (pre-ping, UTC session time zone)."""
    return create_async_engine(
        url,
        pool_pre_ping=True,
        pool_size=10,
        max_overflow=10,
        connect_args={"server_settings": {"timezone": "UTC"}},
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    """Session factory with ``expire_on_commit=False`` (objects stay usable after commit)."""
    return async_sessionmaker(engine, expire_on_commit=False)


@asynccontextmanager
async def transaction(factory: async_sessionmaker[AsyncSession]) -> AsyncIterator[AsyncSession]:
    """Yield a session inside a transaction: commit on success, roll back on any exception."""
    async with factory() as session, session.begin():
        yield session
