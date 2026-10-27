"""Transactional outbox (D-014): side effects that must not be lost are written as rows in the same
transaction as the change that causes them, then delivered by the worker.

Processing uses short transactions and a lease, so slow work never holds row locks:
1. ``claim``: ``SELECT … FOR UPDATE SKIP LOCKED`` due messages of the given topics, highest priority
   first, set ``locked_until = now + lease`` — commit.
2. The worker runs each handler; a handler opens its own transaction(s) and runs CPU-heavy work
   (the diary text model) in a thread **outside** any transaction.
3. ``complete`` / ``fail`` in a short transaction. If a worker dies, the lease expires and another
   worker picks the message up — so handlers must be idempotent. The lease is longer than the
   slowest handler (the model) or is extended while it runs.
Failures retry with backoff up to ``max_attempts``, then the message becomes ``dead`` (visible to
operators, never silently dropped). Alert topics have the highest priority and their own worker pool
can be run (``--topics``), so an alert never waits behind the model.
Index: ``outbox_message(priority desc, available_at) WHERE status IN ('pending','failed')``.
"""

import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import Index, func, or_, select, text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import Mapped

from kompas.core.db import Base, IdMixin, TimestampMixin


class OutboxStatus(StrEnum):
    PENDING = "pending"
    DONE = "done"
    FAILED = "failed"  # last attempt failed; retried at ``available_at``
    DEAD = "dead"  # ``max_attempts`` exhausted


TOPIC_PRIORITY: Mapping[str, int] = {
    "alert.created": 100,
    "prescription.confirmed": 50,
    "reported_drug.created": 50,
    "diary.model_triage": 10,
}
"""Higher runs first; unknown topics get 0. Alerts never wait behind the text model."""
MAX_BACKOFF_SECONDS = 900.0
CRASH_BASE_DELAY_SECONDS = 30.0


class OutboxMessage(IdMixin, TimestampMixin, Base):
    """One pending side effect. ``topic`` selects the handler (e.g. ``alert.created``)."""

    __tablename__ = "outbox_message"
    __table_args__ = (
        Index(
            "ix_outbox_message_due",
            text("priority DESC"),
            "available_at",
            postgresql_where=text("status IN ('pending', 'failed')"),
        ),
    )

    topic: Mapped[str]
    priority: Mapped[int]
    payload: Mapped[dict[str, Any]]
    status: Mapped[OutboxStatus]
    attempts: Mapped[int]
    max_attempts: Mapped[int]
    available_at: Mapped[datetime]
    locked_until: Mapped[datetime | None]
    last_error: Mapped[str | None]


type Handler = Callable[[async_sessionmaker[AsyncSession], Mapping[str, Any]], Awaitable[None]]
"""A handler gets a session factory (it manages its own short transactions) and the payload."""


async def enqueue(
    session: AsyncSession, topic: str, payload: Mapping[str, Any], *, now: datetime, max_attempts: int = 10
) -> uuid.UUID:
    """Add a message in the caller's transaction (visible only when that commits)."""
    message = OutboxMessage(
        id=uuid.uuid4(),
        created_at=now,
        topic=topic,
        priority=TOPIC_PRIORITY.get(topic, 0),
        payload=dict(payload),
        status=OutboxStatus.PENDING,
        attempts=0,
        max_attempts=max_attempts,
        available_at=now,
        locked_until=None,
        last_error=None,
    )
    session.add(message)
    await session.flush()
    return message.id


async def claim(
    session: AsyncSession, *, topics: Sequence[str] | None, now: datetime, lease_seconds: float, limit: int
) -> list[OutboxMessage]:
    """Lease up to ``limit`` due messages (pending/failed with ``available_at <= now`` and no live
    lease), highest priority first. The caller commits right after.

    A message whose lease expired was being handled by a worker that died (crash, OOM in the model):
    that counts as a failed attempt — it gets the usual backoff, and after ``max_attempts`` it is
    ``dead`` instead of crashing workers forever."""
    open_statuses = (OutboxStatus.PENDING, OutboxStatus.FAILED)
    crashed = (
        OutboxMessage.status.in_(open_statuses),
        OutboxMessage.locked_until.is_not(None),
        OutboxMessage.locked_until <= now,
    )
    await session.execute(
        update(OutboxMessage)
        .where(*crashed, OutboxMessage.attempts >= OutboxMessage.max_attempts)
        .values(status=OutboxStatus.DEAD, locked_until=None, last_error="lease expired: worker died while handling")
    )
    delay = func.least(CRASH_BASE_DELAY_SECONDS * func.power(2, OutboxMessage.attempts - 1), MAX_BACKOFF_SECONDS)
    await session.execute(
        update(OutboxMessage)
        .where(*crashed)
        .values(
            status=OutboxStatus.FAILED,
            locked_until=None,
            last_error="lease expired: worker died while handling",
            available_at=now + func.make_interval(0, 0, 0, 0, 0, 0, delay),
        )
    )
    query = (
        select(OutboxMessage)
        .where(
            OutboxMessage.status.in_((OutboxStatus.PENDING, OutboxStatus.FAILED)),
            OutboxMessage.available_at <= now,
            or_(OutboxMessage.locked_until.is_(None), OutboxMessage.locked_until <= now),
        )
        .order_by(OutboxMessage.priority.desc(), OutboxMessage.available_at)
        .limit(limit)
        .with_for_update(skip_locked=True)
    )
    if topics is not None:
        query = query.where(OutboxMessage.topic.in_(list(topics)))
    messages = list((await session.scalars(query)).all())
    for message in messages:
        message.locked_until = now + timedelta(seconds=lease_seconds)
        message.attempts += 1
    await session.flush()
    return messages


async def complete(session: AsyncSession, message_id: uuid.UUID, *, now: datetime) -> None:
    """Mark done."""
    await session.execute(
        update(OutboxMessage)
        .where(OutboxMessage.id == message_id)
        .values(status=OutboxStatus.DONE, locked_until=None, last_error=None)
    )


async def fail(
    session: AsyncSession, message_id: uuid.UUID, error: str, *, now: datetime, base_delay_seconds: float
) -> None:
    """Record the error; schedule a retry with exponential backoff, or mark ``dead`` after max attempts.
    ``attempts`` was already incremented by ``claim``."""
    message = await session.get(OutboxMessage, message_id, with_for_update=True)
    if message is None:
        return
    message.last_error = error[:2000]
    message.locked_until = None
    if message.attempts >= message.max_attempts:
        message.status = OutboxStatus.DEAD
    else:
        message.status = OutboxStatus.FAILED
        delay = min(base_delay_seconds * 2 ** (message.attempts - 1), MAX_BACKOFF_SECONDS)
        message.available_at = now + timedelta(seconds=delay)
    await session.flush()
