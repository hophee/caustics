"""Planned visits (spec §11 «Приёмы»).

The treating doctor plans, moves, cancels and completes visits; the patient sees them and can ask to
reschedule or cancel — a request the doctor accepts or declines (human-in-the-loop: nothing changes
until the doctor decides). Reminders to the patient (a day and ~2 h before, ``rules/appointments.yaml``)
are in-app notifications sent by the worker, re-checked against the visit's current status and time
in the same statement that records the send. Video calls are out of MVP.
"""

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy import DateTime, String, Uuid, exists, func, literal, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kompas.core.actor import Actor
from kompas.core.db import transaction
from kompas.core.errors import InvalidInputError, InvalidStateError, NotFoundError, PermissionDeniedError, Problem
from kompas.modules.appointments.models import (
    Appointment,
    AppointmentChangeRequest,
    AppointmentMode,
    AppointmentReminder,
    AppointmentStatus,
    RequestKind,
    RequestStatus,
)
from kompas.modules.audit import service as audit
from kompas.modules.cases.models import Visit, VisitKind
from kompas.modules.cases.service import active_case, require_treating_doctor
from kompas.modules.identity.models import Doctor, Patient
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.care_rules import AppointmentRules
from kompas.modules.notifications import service as notifications

_log = logging.getLogger(__name__)
COMPLETE_EARLY = timedelta(hours=12)  # a visit can be marked as held at most this long before its start


def _problem(message: str, pointer: str) -> InvalidInputError:
    return InvalidInputError(message, (Problem("invalid_input", message, pointer),))


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def _check_start(starts_at: datetime, rules: AppointmentRules, now: datetime, pointer: str = "starts_at") -> None:
    if starts_at.tzinfo is None:
        raise _problem("Укажите время с часовым поясом", pointer)
    if starts_at <= now:
        raise _problem("Время приёма должно быть в будущем", pointer)
    if starts_at > now + timedelta(days=rules.plan_ahead_days):
        raise _problem(f"Приём можно запланировать не дальше чем на {rules.plan_ahead_days} дн.", pointer)


def _check_where(mode: AppointmentMode, place: str | None, link: str | None) -> None:
    if mode is AppointmentMode.IN_PERSON and not place:
        raise _problem("Укажите место приёма", "place")
    if link is not None and not link.startswith("https://"):
        raise _problem("Ссылка должна начинаться с https://", "link")


async def _patient_user(session: AsyncSession, patient_id: uuid.UUID) -> uuid.UUID | None:
    return await session.scalar(select(Patient.user_id).where(Patient.id == patient_id))


async def _notify_patient(
    session: AsyncSession, appointment: Appointment, kind: str, now: datetime, **extra: Any
) -> None:
    user_id = await _patient_user(session, appointment.patient_id)
    if user_id is None:  # not activated yet: they will see the visit list on first login
        return
    await notifications.notify(
        session,
        user_id,
        kind,
        {"appointment_id": str(appointment.id), "starts_at": appointment.starts_at.isoformat(), **extra},
        now=now,
        patient_id=appointment.patient_id,
    )


async def plan(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    *,
    submission_id: uuid.UUID,
    starts_at: datetime,
    duration_minutes: int | None,
    kind: VisitKind,
    mode: AppointmentMode,
    place: str | None,
    link: str | None,
    purpose: str | None,
    note_for_patient: str | None,
    now: datetime,
) -> Appointment:
    """The treating doctor plans a visit in the patient's active case. Idempotent per ``submission_id``.

    Raises:
        InvalidInputError: time in the past or too far ahead, in-person without a place, bad link.
        NotFoundError: no access or no active case.
    """
    await require_treating_doctor(session, actor, patient_id)
    assert actor.doctor_id is not None
    existing = await session.scalar(
        select(Appointment).where(Appointment.created_by == actor.user_id, Appointment.submission_id == submission_id)
    )
    if existing is not None:
        if existing.patient_id != patient_id:
            raise InvalidStateError("Этот идентификатор отправки уже использован для другого пациента")
        return existing
    rules = knowledge.appointment_rules
    case_row = await active_case(session, actor, patient_id)
    place, link = _clean(place), _clean(link)
    _check_start(starts_at, rules, now)
    _check_where(mode, place, link)
    appointment = Appointment(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        patient_id=patient_id,
        case_id=case_row.id,
        doctor_id=actor.doctor_id,
        starts_at=starts_at,
        duration_minutes=duration_minutes or rules.default_duration_minutes,
        kind=kind,
        mode=mode,
        place=place,
        link=link,
        purpose=_clean(purpose),
        note_for_patient=_clean(note_for_patient),
        status=AppointmentStatus.PLANNED,
        cancel_reason=None,
        visit_id=None,
        patient_summary=None,
        created_by=actor.user_id,
        submission_id=submission_id,
    )
    session.add(appointment)
    await session.flush()
    await _notify_patient(session, appointment, "appointment_planned", now)
    await audit.record(
        session,
        actor,
        "appointment.planned",
        entity_type="appointment",
        entity_id=appointment.id,
        patient_id=patient_id,
        details={"starts_at": starts_at.isoformat(), "kind": kind.value, "mode": mode.value},
        now=now,
    )
    return appointment


async def _doctor_appointment(session: AsyncSession, actor: Actor, appointment_id: uuid.UUID) -> Appointment:
    found = await session.get(Appointment, appointment_id)
    if found is None:
        raise NotFoundError("Приём не найден")
    try:
        await require_treating_doctor(session, actor, found.patient_id)
    except NotFoundError:
        raise NotFoundError("Приём не найден") from None
    locked = await session.get(Appointment, appointment_id, with_for_update=True, populate_existing=True)
    assert locked is not None
    return locked


def _require_planned(appointment: Appointment) -> None:
    if appointment.status is not AppointmentStatus.PLANNED:
        raise InvalidStateError("Приём уже отменён или проведён")


EDITABLE = frozenset({"starts_at", "duration_minutes", "mode", "place", "link", "purpose", "note_for_patient"})
_PATIENT_VISIBLE = frozenset({"starts_at", "duration_minutes", "mode", "place", "link", "note_for_patient"})


async def update(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    appointment_id: uuid.UUID,
    changes: dict[str, Any],
    *,
    now: datetime,
) -> Appointment:
    """The doctor changes a planned visit (only the fields given). The patient is notified when anything
    they see changes; a pending patient request stays open for the doctor's decision."""
    unknown = set(changes) - EDITABLE
    if unknown:
        raise _problem(f"Нельзя изменить: {', '.join(sorted(unknown))}", sorted(unknown)[0])
    appointment = await _doctor_appointment(session, actor, appointment_id)
    _require_planned(appointment)
    rules = knowledge.appointment_rules
    for key in ("place", "link", "purpose", "note_for_patient"):
        if key in changes:
            changes[key] = _clean(changes[key])
    if changes.get("duration_minutes") is None:
        changes.pop("duration_minutes", None)
    if "mode" in changes and changes["mode"] is None:
        raise _problem("Укажите формат приёма", "mode")
    if "starts_at" in changes:
        if changes["starts_at"] is None:
            raise _problem("Укажите время приёма", "starts_at")
        _check_start(changes["starts_at"], rules, now)
    mode = changes.get("mode", appointment.mode)
    _check_where(AppointmentMode(mode), changes.get("place", appointment.place), changes.get("link", appointment.link))
    changed = {k for k, v in changes.items() if getattr(appointment, k) != v}
    if not changed:
        return appointment
    previous_start = appointment.starts_at
    for key in changed:
        setattr(appointment, key, changes[key])
    appointment.updated_at = now
    if changed & _PATIENT_VISIBLE:
        await _notify_patient(
            session, appointment, "appointment_changed", now, previous_starts_at=previous_start.isoformat()
        )
    await audit.record(
        session,
        actor,
        "appointment.updated",
        entity_type="appointment",
        entity_id=appointment.id,
        patient_id=appointment.patient_id,
        details={"fields": sorted(changed), "previous_starts_at": previous_start.isoformat()},
        now=now,
    )
    return appointment


async def _close_pending(session: AsyncSession, appointment_id: uuid.UUID, actor: Actor, now: datetime) -> None:
    pending = await session.scalar(
        select(AppointmentChangeRequest)
        .where(
            AppointmentChangeRequest.appointment_id == appointment_id,
            AppointmentChangeRequest.status == RequestStatus.PENDING,
        )
        .with_for_update()
    )
    if pending is not None:
        pending.status, pending.decided_by, pending.decided_at = RequestStatus.CLOSED, actor.user_id, now


async def cancel(
    session: AsyncSession, actor: Actor, appointment_id: uuid.UUID, *, reason: str | None, now: datetime
) -> Appointment:
    """The doctor cancels a planned visit; the patient is notified, reminders stop."""
    appointment = await _doctor_appointment(session, actor, appointment_id)
    _require_planned(appointment)
    appointment.status, appointment.cancel_reason, appointment.updated_at = (
        AppointmentStatus.CANCELLED,
        _clean(reason),
        now,
    )
    await _close_pending(session, appointment.id, actor, now)
    await _notify_patient(session, appointment, "appointment_cancelled", now)
    await audit.record(
        session,
        actor,
        "appointment.cancelled",
        entity_type="appointment",
        entity_id=appointment.id,
        patient_id=appointment.patient_id,
        details={"by": "doctor", "reason": appointment.cancel_reason},
        now=now,
    )
    return appointment


async def complete(
    session: AsyncSession,
    actor: Actor,
    appointment_id: uuid.UUID,
    *,
    visit_id: uuid.UUID | None,
    patient_summary: str | None,
    now: datetime,
) -> Appointment:
    """The doctor marks the visit as held, optionally linking the visit record and writing a summary for
    the patient (the clinical record itself is not written for the patient and is never shown).

    Raises:
        InvalidInputError: the visit record belongs to another case.
        InvalidStateError: the visit was cancelled, or its time has not come yet.
    """
    appointment = await _doctor_appointment(session, actor, appointment_id)
    _require_planned(appointment)
    if appointment.starts_at > now + COMPLETE_EARLY:
        raise InvalidStateError("Приём ещё не наступил")
    if visit_id is not None:
        visit_case = await session.scalar(select(Visit.case_id).where(Visit.id == visit_id))
        if visit_case != appointment.case_id:
            raise _problem("Запись приёма не найдена в этом случае", "visit_id")
    appointment.status, appointment.visit_id, appointment.updated_at = AppointmentStatus.COMPLETED, visit_id, now
    appointment.patient_summary = _clean(patient_summary)
    await _close_pending(session, appointment.id, actor, now)
    if appointment.patient_summary:
        await _notify_patient(session, appointment, "appointment_summary", now)
    await audit.record(
        session,
        actor,
        "appointment.completed",
        entity_type="appointment",
        entity_id=appointment.id,
        patient_id=appointment.patient_id,
        details={"visit_id": str(visit_id) if visit_id else None, "summary": appointment.patient_summary is not None},
        now=now,
    )
    return appointment


async def set_summary(
    session: AsyncSession, actor: Actor, appointment_id: uuid.UUID, *, patient_summary: str | None, now: datetime
) -> Appointment:
    """The doctor writes or corrects the patient-facing summary of a held visit."""
    appointment = await _doctor_appointment(session, actor, appointment_id)
    if appointment.status is not AppointmentStatus.COMPLETED:
        raise InvalidStateError("Итоги пишутся после проведённого приёма")
    appointment.patient_summary, appointment.updated_at = _clean(patient_summary), now
    if appointment.patient_summary:
        await _notify_patient(session, appointment, "appointment_summary", now)
    await audit.record(
        session,
        actor,
        "appointment.summary_set",
        entity_type="appointment",
        entity_id=appointment.id,
        patient_id=appointment.patient_id,
        details={"empty": appointment.patient_summary is None},
        now=now,
    )
    return appointment


@dataclass(frozen=True, slots=True)
class AppointmentView:
    appointment: Appointment
    doctor_name: str
    request: AppointmentChangeRequest | None  # pending, else the latest decided one


async def views(session: AsyncSession, rows: list[Appointment]) -> list[AppointmentView]:
    if not rows:
        return []
    ids = [a.id for a in rows]
    names: dict[uuid.UUID, str] = {
        doctor_id: name
        for doctor_id, name in await session.execute(
            select(Doctor.id, Doctor.full_name).where(Doctor.id.in_({a.doctor_id for a in rows}))
        )
    }
    requests: dict[uuid.UUID, AppointmentChangeRequest] = {}
    for r in await session.scalars(
        select(AppointmentChangeRequest)
        .where(AppointmentChangeRequest.appointment_id.in_(ids))
        .order_by(AppointmentChangeRequest.created_at)
    ):
        current = requests.get(r.appointment_id)
        if current is None or current.status is not RequestStatus.PENDING:
            requests[r.appointment_id] = r
    return [AppointmentView(a, names.get(a.doctor_id, ""), requests.get(a.id)) for a in rows]


def _scoped(query: Any, scope: str, now: datetime) -> Any:
    upcoming = (Appointment.status == AppointmentStatus.PLANNED) & (
        Appointment.starts_at + func.make_interval(0, 0, 0, 0, 0, Appointment.duration_minutes) > now
    )
    if scope == "upcoming":
        return query.where(upcoming).order_by(Appointment.starts_at, Appointment.id)
    if scope == "past":
        return query.where(~upcoming).order_by(Appointment.starts_at.desc(), Appointment.id)
    return query.order_by(Appointment.starts_at.desc(), Appointment.id)


async def for_doctor(
    session: AsyncSession,
    actor: Actor,
    patient_id: uuid.UUID,
    *,
    scope: str,
    now: datetime,
    offset: int = 0,
    limit: int = 50,
) -> list[AppointmentView]:
    """The patient's visits for the doctor: ``upcoming`` (nearest first), ``past`` or ``all``."""
    await require_treating_doctor(session, actor, patient_id)
    query = _scoped(select(Appointment).where(Appointment.patient_id == patient_id), scope, now)
    return await views(session, list((await session.scalars(query.offset(offset).limit(limit))).all()))


async def schedule(
    session: AsyncSession, actor: Actor, *, start: datetime, end: datetime, limit: int = 200
) -> list[tuple[AppointmentView, str]]:
    """The doctor's planned visits across all their patients in [start, end): (view, patient name)."""
    if not actor.is_doctor or actor.doctor_id is None:
        raise PermissionDeniedError("Действие доступно только врачу")
    rows = (
        await session.execute(
            select(Appointment, Patient.full_name)
            .join(Patient, Patient.id == Appointment.patient_id)
            .where(
                Patient.treating_doctor_id == actor.doctor_id,
                Appointment.status == AppointmentStatus.PLANNED,
                Appointment.starts_at >= start,
                Appointment.starts_at < end,
            )
            .order_by(Appointment.starts_at, Appointment.id)
            .limit(limit)
        )
    ).all()
    found = await views(session, [a for a, _ in rows])
    return list(zip(found, [name for _, name in rows], strict=True))


async def pending_requests(session: AsyncSession, actor: Actor) -> list[tuple[AppointmentChangeRequest, Appointment]]:
    """Patient requests waiting for the doctor's decision, across all their patients (oldest first)."""
    if not actor.is_doctor or actor.doctor_id is None:
        raise PermissionDeniedError("Действие доступно только врачу")
    rows = await session.execute(
        select(AppointmentChangeRequest, Appointment)
        .join(Appointment, Appointment.id == AppointmentChangeRequest.appointment_id)
        .join(Patient, Patient.id == Appointment.patient_id)
        .where(Patient.treating_doctor_id == actor.doctor_id, AppointmentChangeRequest.status == RequestStatus.PENDING)
        .order_by(AppointmentChangeRequest.created_at)
        .limit(200)
    )
    return [(r, a) for r, a in rows.all()]


async def for_patient(
    session: AsyncSession, actor: Actor, *, scope: str, now: datetime, offset: int = 0, limit: int = 50
) -> list[AppointmentView]:
    """The patient's own visits (``upcoming`` / ``past`` / ``all``)."""
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    query = _scoped(select(Appointment).where(Appointment.patient_id == actor.patient_id), scope, now)
    return await views(session, list((await session.scalars(query.offset(offset).limit(limit))).all()))


async def request_change(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    appointment_id: uuid.UUID,
    *,
    submission_id: uuid.UUID,
    kind: RequestKind,
    proposed_starts_at: datetime | None,
    reason: str | None,
    now: datetime,
) -> AppointmentChangeRequest:
    """The patient asks to reschedule or cancel; nothing changes until the doctor decides.

    Raises:
        InvalidStateError: the visit is not planned, too close, or a request is already open.
        InvalidInputError: a proposed time in the past or too far ahead.
    """
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    existing = await session.scalar(
        select(AppointmentChangeRequest).where(
            AppointmentChangeRequest.patient_id == actor.patient_id,
            AppointmentChangeRequest.submission_id == submission_id,
        )
    )
    if existing is not None:
        return existing
    found = await session.get(Appointment, appointment_id)
    if found is None or found.patient_id != actor.patient_id:
        raise NotFoundError("Приём не найден")
    appointment = await session.get(Appointment, appointment_id, with_for_update=True, populate_existing=True)
    assert appointment is not None
    _require_planned(appointment)
    rules = knowledge.appointment_rules
    if appointment.starts_at - timedelta(minutes=rules.request_until_minutes) <= now:
        raise InvalidStateError("До приёма осталось мало времени — свяжитесь с клиникой")
    if kind is RequestKind.CANCEL:
        proposed_starts_at = None
    elif proposed_starts_at is not None:
        _check_start(proposed_starts_at, rules, now, "proposed_starts_at")
    pending = await session.scalar(
        select(AppointmentChangeRequest.id).where(
            AppointmentChangeRequest.appointment_id == appointment.id,
            AppointmentChangeRequest.status == RequestStatus.PENDING,
        )
    )
    if pending is not None:
        raise InvalidStateError("Просьба по этому приёму уже отправлена и ждёт ответа врача")
    request = AppointmentChangeRequest(
        id=uuid.uuid4(),
        created_at=now,
        appointment_id=appointment.id,
        patient_id=actor.patient_id,
        kind=kind,
        proposed_starts_at=proposed_starts_at,
        reason=_clean(reason),
        submission_id=submission_id,
        status=RequestStatus.PENDING,
        reply=None,
        decided_by=None,
        decided_at=None,
    )
    session.add(request)
    await session.flush()
    doctor_user = await session.scalar(
        select(Doctor.user_id)
        .join(Patient, Patient.treating_doctor_id == Doctor.id)
        .where(Patient.id == actor.patient_id)
    )
    if doctor_user is not None:
        await notifications.notify(
            session,
            doctor_user,
            "appointment_request",
            {
                "request_id": str(request.id),
                "appointment_id": str(appointment.id),
                "patient_id": str(actor.patient_id),
                "kind": kind.value,
            },
            now=now,
            patient_id=actor.patient_id,
        )
    await audit.record(
        session,
        actor,
        "appointment.change_requested",
        entity_type="appointment",
        entity_id=appointment.id,
        patient_id=actor.patient_id,
        details={
            "kind": kind.value,
            "proposed_starts_at": proposed_starts_at.isoformat() if proposed_starts_at else None,
        },
        now=now,
    )
    return request


async def decide(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    request_id: uuid.UUID,
    *,
    accept: bool,
    starts_at: datetime | None,
    reply: str | None,
    now: datetime,
) -> tuple[AppointmentChangeRequest, Appointment]:
    """The doctor accepts or declines a patient request. Accepting a reschedule moves the visit to
    ``starts_at`` (default: the patient's proposed time); accepting a cancel cancels it.

    Raises:
        InvalidStateError: the request is already decided.
        InvalidInputError: a reschedule accepted without a valid new time.
    """
    found = await session.get(AppointmentChangeRequest, request_id)
    if found is None:
        raise NotFoundError("Просьба не найдена")
    try:
        await require_treating_doctor(session, actor, found.patient_id)
    except NotFoundError:
        raise NotFoundError("Просьба не найдена") from None
    appointment = await session.get(Appointment, found.appointment_id, with_for_update=True, populate_existing=True)
    request = await session.get(AppointmentChangeRequest, request_id, with_for_update=True, populate_existing=True)
    assert appointment is not None and request is not None
    if request.status is not RequestStatus.PENDING:
        raise InvalidStateError("Решение по этой просьбе уже принято")
    previous_start = appointment.starts_at
    if accept:
        _require_planned(appointment)
        if request.kind is RequestKind.RESCHEDULE:
            new_start = starts_at or request.proposed_starts_at
            if new_start is None:
                raise _problem("Укажите новое время приёма", "starts_at")
            _check_start(new_start, knowledge.appointment_rules, now)
            appointment.starts_at = new_start
        else:
            appointment.status = AppointmentStatus.CANCELLED
            appointment.cancel_reason = "по просьбе пациента" + (f": {request.reason}" if request.reason else "")
        appointment.updated_at = now
    request.status = RequestStatus.ACCEPTED if accept else RequestStatus.DECLINED
    request.reply, request.decided_by, request.decided_at = _clean(reply), actor.user_id, now
    await _notify_patient(
        session,
        appointment,
        "appointment_request_decided",
        now,
        request_id=str(request.id),
        request_kind=request.kind.value,
        accepted=accept,
    )
    await audit.record(
        session,
        actor,
        "appointment.request_decided",
        entity_type="appointment",
        entity_id=appointment.id,
        patient_id=appointment.patient_id,
        details={
            "request_id": str(request.id),
            "kind": request.kind.value,
            "accepted": accept,
            "previous_starts_at": previous_start.isoformat(),
            "starts_at": appointment.starts_at.isoformat(),
        },
        now=now,
    )
    return request, appointment


async def send_due_reminders(factory: async_sessionmaker[AsyncSession], knowledge: Knowledge, *, now: datetime) -> int:
    """Remind patients of planned visits (offsets from ``rules/appointments.yaml``). Each send is
    recorded first, unique per visit, start time and offset, and only while the visit is still planned
    at that same time (checked in the same statement), so a cancelled or moved visit gets no stale
    reminder and a moved one gets fresh ones. Reminders later than ``send_grace_minutes`` are dropped.
    Returns the number sent."""
    rules = knowledge.appointment_rules
    if not rules.reminders:
        return 0
    grace = timedelta(minutes=rules.send_grace_minutes)
    horizon = now + timedelta(minutes=max(r.before_minutes for r in rules.reminders))
    async with factory() as session:
        due = (
            await session.execute(
                select(Appointment.id, Appointment.starts_at).where(
                    Appointment.status == AppointmentStatus.PLANNED,
                    Appointment.starts_at > now,
                    Appointment.starts_at <= horizon,
                )
            )
        ).all()
    sent = 0
    for appointment_id, starts_at in due:
        for reminder in rules.reminders:
            send_at = starts_at - timedelta(minutes=reminder.before_minutes)
            if not (now - grace <= send_at <= now):
                continue
            try:
                sent += await _remind(factory, appointment_id, starts_at, reminder.code, now)
            except Exception:  # one broken row must not stop reminders for everyone
                _log.exception("appointment reminder failed for %s", appointment_id)
    return sent


async def _remind(
    factory: async_sessionmaker[AsyncSession], appointment_id: uuid.UUID, starts_at: datetime, code: str, now: datetime
) -> int:
    async with transaction(factory) as session:
        still_planned = exists().where(
            Appointment.id == appointment_id,
            Appointment.status == AppointmentStatus.PLANNED,
            Appointment.starts_at == starts_at,
        )
        inserted = await session.scalar(
            pg_insert(AppointmentReminder)
            .from_select(
                ["id", "appointment_id", "starts_at", "code", "sent_at"],
                select(
                    literal(uuid.uuid4(), Uuid),
                    literal(appointment_id, Uuid),
                    literal(starts_at, DateTime(timezone=True)),
                    literal(code, String),
                    literal(now, DateTime(timezone=True)),
                ).where(still_planned),
            )
            .on_conflict_do_nothing(constraint="uq_appointment_reminder_send")
            .returning(AppointmentReminder.id)
        )
        if inserted is None:
            return 0
        appointment = await session.get(Appointment, appointment_id)
        assert appointment is not None
        await _notify_patient(
            session,
            appointment,
            "appointment_reminder",
            now,
            reminder=code,
            mode=appointment.mode.value,
            place=appointment.place,
            link=appointment.link,
        )
        return 1
