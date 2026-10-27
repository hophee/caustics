"""Notifications: a row per message, pushed live over SSE.

Live push across processes: ``notify`` inserts the row and issues ``NOTIFY kompas_notifications,
'<user_id>'`` (delivered at commit). Each API process holds **one** listener connection
(``NotificationHub``) that fans out to in-process queues keyed by user; an SSE stream never holds a
database connection while idle — on a wake-up it reads ``seq > last_sent`` with a short session.
After the hub reconnects, every stream re-reads from its last sent seq, so nothing is lost.
"""

import asyncio
import contextlib
import hashlib
import hmac
import logging
import secrets
import uuid
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from typing import Any

import asyncpg
from sqlalchemy import func, select, text, update
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kompas.core.actor import Actor
from kompas.core.errors import NotFoundError, UnauthenticatedError
from kompas.modules.notifications.models import Notification, StreamTicket

CHANNEL = "kompas_notifications"
TICKET_SECONDS = 60
HEARTBEAT_SECONDS = 15.0
type Wake = asyncio.Queue[uuid.UUID | None]
_log = logging.getLogger(__name__)


def _ticket_hash(ticket: str, secret: str) -> str:
    return hmac.new(secret.encode(), ticket.encode(), hashlib.sha256).hexdigest()


async def _pg_notify(session: AsyncSession, user_id: uuid.UUID, resend: uuid.UUID | None = None) -> None:
    """Payload ``<user_id>`` (new rows: streams re-read ``seq > last``) or ``<user_id>:<notification_id>``
    (re-send one existing notification, for alert retries)."""
    payload = f"{user_id}:{resend}" if resend else str(user_id)
    await session.execute(text("SELECT pg_notify(:channel, :payload)"), {"channel": CHANNEL, "payload": payload})


async def notify(
    session: AsyncSession,
    user_id: uuid.UUID,
    kind: str,
    payload: dict[str, Any],
    *,
    now: datetime,
    patient_id: uuid.UUID | None = None,
) -> Notification:
    """Take ``pg_advisory_xact_lock(hashtext(user_id))``, insert, NOTIFY — in the caller's transaction."""
    await session.execute(text("SELECT pg_advisory_xact_lock(hashtext(:user))"), {"user": str(user_id)})
    notification = Notification(
        id=uuid.uuid4(),
        created_at=now,
        user_id=user_id,
        kind=kind,
        payload=payload,
        read_at=None,
        patient_id=patient_id,
        hidden_at=None,
    )
    session.add(notification)
    await session.flush()
    await _pg_notify(session, user_id)
    return notification


async def renotify(session: AsyncSession, notification_id: uuid.UUID) -> None:
    """Re-send an existing notification (alert retries): NOTIFY only, no new row. Open streams emit
    that notification again (same SSE id), so a client that missed it gets another chance."""
    user_id = await session.scalar(select(Notification.user_id).where(Notification.id == notification_id))
    if user_id is not None:
        await _pg_notify(session, user_id, resend=notification_id)


async def after(session: AsyncSession, actor: Actor, *, after_seq: int | None, limit: int) -> list[Notification]:
    """The actor's notifications with ``seq > after_seq`` (oldest first; unread only if None)."""
    query = select(Notification).where(Notification.user_id == actor.user_id, Notification.hidden_at.is_(None))
    if after_seq is None:
        query = query.where(Notification.read_at.is_(None))
    else:
        query = query.where(Notification.seq > after_seq)
    return list((await session.scalars(query.order_by(Notification.seq).limit(limit))).all())


async def mark_read(session: AsyncSession, actor: Actor, notification_id: uuid.UUID, *, now: datetime) -> None:
    """Mark one of the actor's notifications read."""
    changed = await session.execute(
        update(Notification)
        .where(Notification.id == notification_id, Notification.user_id == actor.user_id)
        .values(read_at=func.coalesce(Notification.read_at, now))
        .returning(Notification.id)
    )
    if changed.scalar_one_or_none() is None:
        raise NotFoundError("Уведомление не найдено")


async def issue_ticket(session: AsyncSession, actor: Actor, *, secret: str, now: datetime) -> str:
    """Create a single-use 60 s stream ticket (stored as an HMAC)."""
    ticket = secrets.token_urlsafe(32)
    session.add(
        StreamTicket(
            id=uuid.uuid4(),
            created_at=now,
            ticket_hash=_ticket_hash(ticket, secret),
            user_id=actor.user_id,
            expires_at=now + timedelta(seconds=TICKET_SECONDS),
            used_at=None,
        )
    )
    await session.flush()
    return ticket


async def consume_ticket(session: AsyncSession, ticket: str, *, secret: str, now: datetime) -> uuid.UUID:
    """Atomically consume a ticket and return its user id.

    Raises:
        UnauthenticatedError: unknown, used or expired ticket.
    """
    user_id = await session.scalar(
        update(StreamTicket)
        .where(
            StreamTicket.ticket_hash == _ticket_hash(ticket, secret),
            StreamTicket.used_at.is_(None),
            StreamTicket.expires_at > now,
        )
        .values(used_at=now)
        .returning(StreamTicket.user_id)
    )
    if user_id is None:
        raise UnauthenticatedError("Билет потока недействителен или истёк")
    return uuid.UUID(str(user_id))


class NotificationHub:
    """One LISTEN connection per process; wakes the streams of the notified user."""

    def __init__(self, database_url: str) -> None:
        self._dsn = make_url(database_url).set(drivername="postgresql").render_as_string(hide_password=False)
        self._queues: dict[uuid.UUID, set[Wake]] = {}
        self._task: asyncio.Task[None] | None = None
        self._connected = asyncio.Event()

    async def start(self) -> None:
        """Open the listener connection (reconnects with backoff; on reconnect wakes everyone)."""
        if self._task is None:
            self._task = asyncio.create_task(self._run(), name="notification-hub")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._connected.wait(), timeout=5)

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task
            self._task = None

    def subscribe(self, user_id: uuid.UUID) -> Wake:
        """A wake-up queue for one stream: ``None`` = new rows, a UUID = re-send that notification."""
        queue: Wake = asyncio.Queue(maxsize=64)
        self._queues.setdefault(user_id, set()).add(queue)
        return queue

    def _wake(self, queues: set[Wake], resend: uuid.UUID | None = None) -> None:
        for queue in list(queues):
            with contextlib.suppress(asyncio.QueueFull):  # a full queue already has wake-ups pending
                queue.put_nowait(resend)

    def _on_notify(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        user, _, resend = payload.partition(":")
        with contextlib.suppress(ValueError):
            self._wake(self._queues.get(uuid.UUID(user), set()), uuid.UUID(resend) if resend else None)

    def wake_all(self) -> None:
        """Wake every stream (after a reconnect a NOTIFY may have been missed)."""
        for queues in list(self._queues.values()):
            self._wake(queues)

    async def _run(self) -> None:
        delay = 0.5
        while True:
            try:
                connection = await asyncpg.connect(self._dsn)
            except Exception as exc:
                _log.warning("notification hub: connect failed (%s); retrying in %.1fs", exc, delay)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30.0)
                continue
            lost = asyncio.Event()
            connection.add_termination_listener(lambda _c, lost=lost: lost.set())
            try:
                await connection.add_listener(CHANNEL, self._on_notify)
                delay = 0.5
                self._connected.set()
                self.wake_all()
                await lost.wait()
            except Exception as exc:  # never let the hub task die: streams would fall back to polling
                _log.warning("notification hub: listener failed (%s)", exc)
                await asyncio.sleep(delay)
            finally:
                self._connected.clear()
                with contextlib.suppress(Exception):
                    await connection.close(timeout=2)

    def unsubscribe(self, user_id: uuid.UUID, queue: Wake) -> None:
        queues = self._queues.get(user_id)
        if queues is not None:
            queues.discard(queue)
            if not queues:
                del self._queues[user_id]


async def stream(
    hub: NotificationHub,
    factory: async_sessionmaker[AsyncSession],
    user_id: uuid.UUID,
    *,
    last_event_id: int | None,
) -> AsyncIterator[Notification | None]:
    """Yield notifications after ``last_event_id`` as they arrive; yields None every 15 s as a
    heartbeat (the router sends an SSE comment). Holds no DB connection while waiting. Without
    ``last_event_id`` the stream starts at the newest notification (clients that just listed their
    notifications pass that list's last seq as ``last_event_id``, so nothing falls in between).
    Alert retries re-emit their notification (same id)."""
    queue = hub.subscribe(user_id)
    try:
        last = last_event_id
        resends: set[uuid.UUID] = set()
        if last is None:
            async with factory() as session:
                last = await session.scalar(
                    select(func.coalesce(func.max(Notification.seq), 0)).where(Notification.user_id == user_id)
                )
        while True:
            async with factory() as session:
                rows = list(
                    (
                        await session.scalars(
                            select(Notification)
                            .where(
                                Notification.user_id == user_id,
                                Notification.seq > last,
                                Notification.hidden_at.is_(None),
                            )
                            .order_by(Notification.seq)
                            .limit(100)
                        )
                    ).all()
                )
                again = []
                if resends:
                    again = list(
                        (
                            await session.scalars(
                                select(Notification).where(
                                    Notification.id.in_(resends - {r.id for r in rows}),
                                    Notification.user_id == user_id,
                                    Notification.hidden_at.is_(None),
                                    Notification.seq <= last,
                                )
                            )
                        ).all()
                    )
                    resends.clear()
            for row in rows:
                last = row.seq
                yield row
            for row in again:
                yield row
            if len(rows) == 100:
                continue
            try:
                item = await asyncio.wait_for(queue.get(), timeout=HEARTBEAT_SECONDS)
            except TimeoutError:
                yield None
                continue
            while True:  # take every pending wake-up at once
                if item is not None:
                    resends.add(item)
                try:
                    item = queue.get_nowait()
                except asyncio.QueueEmpty:
                    break
    finally:
        hub.unsubscribe(user_id, queue)
