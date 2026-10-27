"""Alert use cases.

``raise_alert`` writes the alert and an ``alert.created`` outbox message in the caller's transaction
(D-014). The worker creates **one** in-app notification for the treating doctor and marks the alert
sent. Without a delivery acknowledgement the worker re-NOTIFIes the same notification (no new rows)
with exponential backoff (30 s, 1 min, 2 min … capped at 15 min) for at most ``max_attempts``; then
the alert is flagged ``undelivered`` (escalation rules N1/N2/N3 are still open). Retry handlers are
no-ops unless the alert is still ``sent``.
"""

import uuid
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core import outbox
from kompas.core.actor import Actor
from kompas.core.errors import NotFoundError, PermissionDeniedError
from kompas.modules.audit import service as audit
from kompas.modules.identity.models import Doctor, Patient
from kompas.modules.identity.service import treating_doctor
from kompas.modules.knowledge.triage_rules import UrgentScenario
from kompas.modules.monitoring.domain.alerts import AlertEvent, AlertState, AlertStatus, transition
from kompas.modules.monitoring.models import AlertLevel, AlertRow
from kompas.modules.notifications import service as notifications
from kompas.modules.notifications.models import Notification

RETRY_BASE_SECONDS = 30.0
RETRY_MAX_SECONDS = 900.0
TOPIC = "alert.created"
_LEVEL_ORDER = {AlertLevel.URGENT: 0, AlertLevel.HIGH: 1, AlertLevel.PLANNED: 2}


def retry_delay(attempt: int) -> timedelta:
    """30 s, 1 min, 2 min … capped at 15 min (``attempt`` starts at 0)."""
    return timedelta(seconds=min(RETRY_BASE_SECONDS * 2**attempt, RETRY_MAX_SECONDS))


def _state(row: AlertRow) -> AlertState:
    return AlertState(
        status=row.status,
        created_at=row.created_at,
        sent_at=row.sent_at,
        delivered_at=row.delivered_at,
        accepted_at=row.accepted_at,
        handled_at=row.handled_at,
        handled_note=row.handled_note,
    )


def _apply(row: AlertRow, state: AlertState) -> None:
    row.status = state.status
    row.sent_at = state.sent_at
    row.delivered_at = state.delivered_at
    row.accepted_at = state.accepted_at
    row.handled_at = state.handled_at
    row.handled_note = state.handled_note
    if state.status is not AlertStatus.SENT:
        row.next_retry_at = None


def _notification_payload(row: AlertRow) -> dict[str, Any]:
    return {
        "alert_id": str(row.id),
        "patient_id": str(row.patient_id),
        "level": row.level.value,
        "scenario": row.scenario.value if row.scenario else None,
        "reason": row.reason,
        "source": row.source,
    }


async def _doctor_alert(session: AsyncSession, actor: Actor, alert_id: uuid.UUID) -> AlertRow:
    if not actor.is_doctor:
        raise PermissionDeniedError("Действие доступно только врачу")
    row = await session.get(AlertRow, alert_id)
    if row is None or row.doctor_id != actor.doctor_id:
        raise NotFoundError("Сигнал не найден")
    locked = await session.get(AlertRow, alert_id, with_for_update=True, populate_existing=True)
    if locked is None or locked.doctor_id != actor.doctor_id:  # re-check: a transfer may have won the race
        raise NotFoundError("Сигнал не найден")
    return locked


async def raise_alert(
    session: AsyncSession,
    *,
    patient_id: uuid.UUID,
    case_id: uuid.UUID | None,
    level: AlertLevel,
    scenario: UrgentScenario | None = None,
    source: str,
    source_id: uuid.UUID,
    reason: str,
    details: Mapping[str, Any],
    dedup_key: str,
    knowledge_version: str,
    now: datetime,
) -> AlertRow:
    """Create the alert (idempotent on ``dedup_key``) routed to the treating doctor and enqueue
    delivery. Never raises for routing: every patient has a treating doctor.

    Raises:
        ValueError: ``scenario`` missing for an urgent alert or given for another level (D-132).
    """
    if (level is AlertLevel.URGENT) != (scenario is not None):
        raise ValueError("scenario is required for urgent alerts and only for them")
    doctor = await treating_doctor(session, patient_id)
    inserted = await session.scalar(
        insert(AlertRow)
        .values(
            id=uuid.uuid4(),
            created_at=now,
            patient_id=patient_id,
            case_id=case_id,
            doctor_id=doctor.id,
            level=level,
            scenario=scenario,
            source=source,
            source_id=source_id,
            reason=reason,
            details=dict(details),
            knowledge_version=knowledge_version,
            dedup_key=dedup_key,
            status=AlertStatus.CREATED,
            notification_id=None,
            attempts=0,
            next_retry_at=None,
            undelivered=False,
            sent_at=None,
            delivered_at=None,
            accepted_at=None,
            handled_at=None,
            handled_note=None,
        )
        .on_conflict_do_nothing(index_elements=[AlertRow.dedup_key])
        .returning(AlertRow.id)
    )
    if inserted is not None:
        await outbox.enqueue(session, TOPIC, {"alert_id": str(inserted)}, now=now)
    row = await session.scalar(select(AlertRow).where(AlertRow.dedup_key == dedup_key))
    assert row is not None
    return row


async def deliver(session: AsyncSession, payload: Mapping[str, Any], *, now: datetime) -> None:
    """Outbox handler for ``alert.created``: no-op if ``alert.notification_id`` is already set
    (idempotent under lease expiry); otherwise notification + status sent + schedule retry check."""
    row = await session.get(AlertRow, uuid.UUID(str(payload["alert_id"])), with_for_update=True)
    if row is None or row.notification_id is not None or row.status is not AlertStatus.CREATED:
        return
    user_id = await session.scalar(select(Doctor.user_id).where(Doctor.id == row.doctor_id))
    assert user_id is not None
    notification = await notifications.notify(
        session, user_id, "alert", _notification_payload(row), now=now, patient_id=row.patient_id
    )
    row.notification_id = notification.id
    _apply(row, transition(_state(row), AlertEvent.DISPATCH, now))
    row.next_retry_at = now + retry_delay(0)


async def retry_undelivered(session: AsyncSession, *, now: datetime, max_attempts: int, limit: int = 100) -> int:
    """Worker tick: claim due ``sent`` alerts with ``FOR UPDATE SKIP LOCKED`` (no double re-NOTIFY
    across workers), re-NOTIFY with backoff, flag ``undelivered`` after ``max_attempts``. Returns how
    many were touched."""
    rows = (
        await session.scalars(
            select(AlertRow)
            .where(
                AlertRow.status == AlertStatus.SENT,
                AlertRow.undelivered.is_(False),
                AlertRow.next_retry_at <= now,
            )
            .order_by(AlertRow.next_retry_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for row in rows:
        if row.attempts >= max_attempts:
            row.undelivered = True
            row.next_retry_at = None
            continue
        if row.notification_id is not None:
            await notifications.renotify(session, row.notification_id)
        row.attempts += 1
        row.next_retry_at = now + retry_delay(row.attempts)
    return len(rows)


async def confirm_delivery(session: AsyncSession, actor: Actor, alert_id: uuid.UUID, *, now: datetime) -> AlertRow:
    """Doctor's browser acknowledges receipt (→ delivered)."""
    row = await _doctor_alert(session, actor, alert_id)
    if row.status in (AlertStatus.CREATED, AlertStatus.DELIVERED):
        return row  # nothing sent yet / already acknowledged: idempotent
    _apply(row, transition(_state(row), AlertEvent.DELIVERY_CONFIRMED, now))
    row.undelivered = False
    return row


async def accept(session: AsyncSession, actor: Actor, alert_id: uuid.UUID, *, now: datetime) -> AlertRow:
    """Doctor opens the alert (→ accepted, allowed from created/sent/delivered); audited."""
    row = await _doctor_alert(session, actor, alert_id)
    if row.status in (AlertStatus.ACCEPTED, AlertStatus.HANDLED):
        return row
    _apply(row, transition(_state(row), AlertEvent.ACCEPT, now))
    row.undelivered = False
    await audit.record(
        session,
        actor,
        "alert.accepted",
        entity_type="alert",
        entity_id=row.id,
        patient_id=row.patient_id,
        details={"level": row.level.value},
        now=now,
    )
    return row


async def handle(session: AsyncSession, actor: Actor, alert_id: uuid.UUID, note: str, *, now: datetime) -> AlertRow:
    """Doctor records what was done (→ handled, note required); audited."""
    row = await _doctor_alert(session, actor, alert_id)
    if row.status is not AlertStatus.ACCEPTED and row.status is not AlertStatus.HANDLED:
        _apply(row, transition(_state(row), AlertEvent.ACCEPT, now))  # handling implies opening
    _apply(row, transition(_state(row), AlertEvent.HANDLE, now, note.strip()))
    await audit.record(
        session,
        actor,
        "alert.handled",
        entity_type="alert",
        entity_id=row.id,
        patient_id=row.patient_id,
        details={"level": row.level.value},
        now=now,
    )
    return row


async def open_alerts(session: AsyncSession, actor: Actor, *, offset: int, limit: int) -> tuple[list[AlertRow], int]:
    """The doctor's unhandled alerts: undelivered and most urgent first («опасность сейчас» before
    «уточнить состояние»), then oldest."""
    if not actor.is_doctor:
        raise PermissionDeniedError("Действие доступно только врачу")
    where = (AlertRow.doctor_id == actor.doctor_id, AlertRow.status != AlertStatus.HANDLED)
    total = await session.scalar(select(func.count()).select_from(AlertRow).where(*where))
    level_rank = case(_LEVEL_ORDER, value=AlertRow.level, else_=9)
    danger_first = case((AlertRow.scenario == UrgentScenario.DANGER_NOW, 0), else_=1)
    rows = await session.scalars(
        select(AlertRow)
        .where(*where)
        .order_by(AlertRow.undelivered.desc(), level_rank, danger_first, AlertRow.created_at)
        .offset(offset)
        .limit(limit)
    )
    return list(rows.all()), total or 0


async def reroute_open_alerts(
    session: AsyncSession, patient_id: uuid.UUID, new_doctor_id: uuid.UUID, *, now: datetime
) -> int:
    """Called by ``identity.transfer_patient``: move unhandled alerts to the new doctor, reset
    ``attempts``/``next_retry_at``/``undelivered`` and create a fresh notification for the new doctor's
    user. The old doctor's existing notifications for the patient are marked read and hidden from
    their list (they lost access)."""
    new_user = await session.scalar(select(Doctor.user_id).where(Doctor.id == new_doctor_id))
    patient_user = await session.scalar(select(Patient.user_id).where(Patient.id == patient_id))
    assert new_user is not None
    hidden_for = [Notification.patient_id == patient_id, Notification.user_id != new_user]
    if patient_user is not None:
        hidden_for.append(Notification.user_id != patient_user)
    await session.execute(
        update(Notification)
        .where(*hidden_for, Notification.hidden_at.is_(None))
        .values(hidden_at=now, read_at=func.coalesce(Notification.read_at, now))
    )
    rows = (
        await session.scalars(
            select(AlertRow)
            .where(AlertRow.patient_id == patient_id, AlertRow.status != AlertStatus.HANDLED)
            .with_for_update()
        )
    ).all()
    for row in rows:
        row.doctor_id = new_doctor_id
        row.attempts = 0
        row.undelivered = False
        if row.status is AlertStatus.CREATED:
            continue  # not dispatched yet: the outbox message will route it to the new doctor
        notification = await notifications.notify(
            session, new_user, "alert", _notification_payload(row), now=now, patient_id=patient_id
        )
        row.notification_id = notification.id
        row.next_retry_at = now + retry_delay(0) if row.status is AlertStatus.SENT else None
    return len(rows)
