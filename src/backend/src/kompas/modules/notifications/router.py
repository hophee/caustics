"""Endpoints: notification list, read marks, stream ticket, live stream."""

import json
import uuid
from collections.abc import AsyncIterator
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Header, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from kompas.api.deps import ActorDep, ClockDep, SessionDep, SettingsDep, get_session_factory
from kompas.modules.notifications import service
from kompas.modules.notifications.models import Notification

router = APIRouter(tags=["notifications"])


class NotificationOut(BaseModel):
    notification_id: uuid.UUID
    seq: int
    kind: str
    payload: dict[str, Any]
    created_at: datetime
    read: bool

    @classmethod
    def of(cls, n: Notification) -> "NotificationOut":
        return cls(
            notification_id=n.id,
            seq=n.seq,
            kind=n.kind,
            payload=n.payload,
            created_at=n.created_at,
            read=n.read_at is not None,
        )


class TicketOut(BaseModel):
    ticket: str
    expires_in_seconds: int = service.TICKET_SECONDS


@router.get("/me/notifications")
async def get_notifications(
    actor: ActorDep,
    session: SessionDep,
    after_seq: int | None = Query(None, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[NotificationOut]:
    """GET /me/notifications?after_seq&limit."""
    return [NotificationOut.of(n) for n in await service.after(session, actor, after_seq=after_seq, limit=limit)]


@router.post("/me/notifications/{notification_id}/read", status_code=204)
async def post_read(notification_id: uuid.UUID, actor: ActorDep, session: SessionDep, clock: ClockDep) -> None:
    """POST /me/notifications/{id}/read."""
    await service.mark_read(session, actor, notification_id, now=clock.now())


@router.post("/me/notifications/ticket")
async def post_ticket(actor: ActorDep, session: SessionDep, settings: SettingsDep, clock: ClockDep) -> TicketOut:
    """POST /me/notifications/ticket — bearer-authenticated; returns a single-use stream ticket."""
    ticket = await service.issue_ticket(
        session, actor, secret=settings.invite_secret.get_secret_value(), now=clock.now()
    )
    return TicketOut(ticket=ticket)


@router.get("/me/notifications/stream")
async def get_stream(
    request: Request,
    settings: SettingsDep,
    clock: ClockDep,
    ticket: str = Query(...),
    after_seq: int | None = Query(None, ge=0),  # first connection: last seq the client already listed
    last_event_id: str | None = Header(None, alias="Last-Event-ID"),
) -> StreamingResponse:
    """GET /me/notifications/stream?ticket=… — text/event-stream; honours Last-Event-ID; the ticket is
    consumed with a short session that is released before streaming starts."""
    factory = get_session_factory(request)
    async with factory() as session, session.begin():
        user_id = await service.consume_ticket(
            session, ticket, secret=settings.invite_secret.get_secret_value(), now=clock.now()
        )
    last = int(last_event_id) if last_event_id and last_event_id.isdigit() else after_seq
    hub: service.NotificationHub = request.app.state.hub

    async def events() -> AsyncIterator[str]:
        yield "retry: 3000\n\n"
        async for item in service.stream(hub, factory, user_id, last_event_id=last):
            if await request.is_disconnected():
                break
            if item is None:
                yield ": heartbeat\n\n"
                continue
            data = json.dumps(NotificationOut.of(item).model_dump(mode="json"), ensure_ascii=False)
            yield f"id: {item.seq}\nevent: {item.kind}\ndata: {data}\n\n"

    headers = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(events(), media_type="text/event-stream", headers=headers)
