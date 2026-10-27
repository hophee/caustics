"""Endpoints: alerts for the doctor."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from kompas.api.deps import ClockDep, DoctorDep, SessionDep
from kompas.modules.identity.schemas import Page
from kompas.modules.knowledge.triage_rules import UrgentScenario
from kompas.modules.monitoring import service
from kompas.modules.monitoring.domain.alerts import AlertStatus
from kompas.modules.monitoring.models import AlertLevel, AlertRow

router = APIRouter(tags=["alerts"])


class AlertOut(BaseModel):
    alert_id: uuid.UUID
    patient_id: uuid.UUID
    level: AlertLevel
    scenario: UrgentScenario | None  # urgent only: danger_now «опасность сейчас» / check_soon «уточнить состояние»
    status: AlertStatus
    source: str
    source_id: uuid.UUID
    reason: str
    details: dict[str, Any]
    undelivered: bool
    created_at: datetime
    sent_at: datetime | None
    delivered_at: datetime | None
    accepted_at: datetime | None
    handled_at: datetime | None
    handled_note: str | None

    @classmethod
    def of(cls, a: AlertRow) -> "AlertOut":
        return cls(
            alert_id=a.id,
            patient_id=a.patient_id,
            level=a.level,
            scenario=a.scenario,
            status=a.status,
            source=a.source,
            source_id=a.source_id,
            reason=a.reason,
            details=a.details,
            undelivered=a.undelivered,
            created_at=a.created_at,
            sent_at=a.sent_at,
            delivered_at=a.delivered_at,
            accepted_at=a.accepted_at,
            handled_at=a.handled_at,
            handled_note=a.handled_note,
        )


class HandleIn(BaseModel):
    note: str = Field(min_length=1)


@router.get("/alerts")
async def get_alerts(
    actor: DoctorDep,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Page[AlertOut]:
    """GET /alerts — the doctor's open alerts."""
    rows, total = await service.open_alerts(session, actor, offset=offset, limit=limit)
    return Page(items=[AlertOut.of(a) for a in rows], total=total, offset=offset, limit=limit)


@router.post("/alerts/{alert_id}/delivered")
async def post_delivered(alert_id: uuid.UUID, actor: DoctorDep, session: SessionDep, clock: ClockDep) -> AlertOut:
    """POST /alerts/{id}/delivered — browser acknowledgement."""
    return AlertOut.of(await service.confirm_delivery(session, actor, alert_id, now=clock.now()))


@router.post("/alerts/{alert_id}/accept")
async def post_accept(alert_id: uuid.UUID, actor: DoctorDep, session: SessionDep, clock: ClockDep) -> AlertOut:
    """POST /alerts/{id}/accept."""
    return AlertOut.of(await service.accept(session, actor, alert_id, now=clock.now()))


@router.post("/alerts/{alert_id}/handle")
async def post_handle(
    alert_id: uuid.UUID, body: HandleIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> AlertOut:
    """POST /alerts/{id}/handle — body: note."""
    return AlertOut.of(await service.handle(session, actor, alert_id, body.note, now=clock.now()))
