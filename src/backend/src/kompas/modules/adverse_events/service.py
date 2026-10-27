"""Adverse event (НЯ) use cases (spec §9, F3).

The patient reports (symptom, severity, start, suspected drug); a severe report raises an alert at the
level set in ``rules/adverse_events.yaml`` (no alarming screen for the patient — only «Нужна помощь?»,
as everywhere). The treating doctor reviews it — confirm («Зафиксировать НЯ») or dismiss with a reason,
causality, «описано в инструкции», whether Росздравнадзор must be notified — or records one directly.
The system reminds about the report (an open flag in the doctor's list) and never sends it.
"""

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.core.clock import local_date
from kompas.core.errors import InvalidInputError, InvalidStateError, NotFoundError, PermissionDeniedError, Problem
from kompas.core.security import Role
from kompas.modules.adverse_events.models import AdverseEvent, AeStatus, DrugRelation
from kompas.modules.audit import service as audit
from kompas.modules.cases.domain.profile import Tristate
from kompas.modules.cases.models import CaseStatus, MedicalCase
from kompas.modules.cases.service import patient_timezone, require_treating_doctor
from kompas.modules.identity.models import Doctor, Patient
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.monitoring import service as monitoring
from kompas.modules.monitoring.models import AlertLevel
from kompas.modules.notifications import service as notifications
from kompas.modules.therapy.models import Prescription, PrescriptionItem

MAX_AGE_DAYS = 365  # a report about something that started more than a year ago is refused


def _problem(message: str, pointer: str) -> InvalidInputError:
    return InvalidInputError(message, (Problem("invalid_input", message, pointer),))


async def _active_case(session: AsyncSession, patient_id: uuid.UUID) -> MedicalCase:
    found = await session.scalar(
        select(MedicalCase).where(
            MedicalCase.patient_id == patient_id, MedicalCase.status == CaseStatus.ACTIVE, MedicalCase.demo.is_(False)
        )
    )
    if found is None:
        raise InvalidStateError("У пациента нет активного случая")
    return found


async def _validated(
    session: AsyncSession,
    knowledge: Knowledge,
    case_row: MedicalCase,
    *,
    course_id: uuid.UUID | None,
    symptom_code: str,
    symptom_text: str | None,
    severity: str,
    started_on: date,
    today: date,
) -> tuple[str | None, str | None, date | None]:
    """Check the report fields; return (drug name, cleaned free text, last therapy change on/before start)."""
    rules = knowledge.adverse_event_rules
    if rules.symptom_label(symptom_code) is None:
        raise _problem("Неизвестный симптом", "symptom_code")
    text = (symptom_text or "").strip() or None
    if symptom_code == "other" and not text:
        raise _problem("Опишите симптом", "symptom_text")
    if severity not in {s.code for s in rules.severities}:
        raise _problem("Неизвестная тяжесть", "severity")
    if started_on > today or started_on < today - timedelta(days=MAX_AGE_DAYS):
        raise _problem("Дата начала — не в будущем и не раньше чем год назад", "started_on")
    drug_name = None
    if course_id is not None:
        drug_name = await session.scalar(
            select(PrescriptionItem.trade_name)
            .join(Prescription, Prescription.id == PrescriptionItem.prescription_id)
            .where(Prescription.case_id == case_row.id, PrescriptionItem.course_id == course_id)
            .order_by(Prescription.version.desc())
            .limit(1)
        )
        if drug_name is None:
            raise _problem("Этого препарата нет в назначениях", "course_id")
    tz = await patient_timezone(session, case_row.patient_id)
    changes = await session.scalars(
        select(Prescription.confirmed_at)
        .where(Prescription.case_id == case_row.id)
        .order_by(Prescription.confirmed_at.desc())
    )
    last_change = next((local_date(c, tz) for c in changes if local_date(c, tz) <= started_on), None)
    return drug_name, text, last_change


async def report(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    *,
    submission_id: uuid.UUID,
    course_id: uuid.UUID | None,
    symptom_code: str,
    symptom_text: str | None,
    severity: str,
    started_on: date,
    ongoing: bool,
    ended_on: date | None = None,
    now: datetime,
) -> AdverseEvent:
    """Patient reports a side effect. Repeating ``submission_id`` returns the stored report.

    Raises:
        InvalidInputError: unknown symptom/severity, «Другое» without text, bad date, unknown drug.
    """
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    existing = await session.scalar(
        select(AdverseEvent).where(
            AdverseEvent.reported_by == actor.user_id, AdverseEvent.submission_id == submission_id
        )
    )
    if existing is not None:
        return existing
    case_row = await _active_case(session, actor.patient_id)
    today = local_date(now, await patient_timezone(session, actor.patient_id))
    drug_name, text, last_change = await _validated(
        session,
        knowledge,
        case_row,
        course_id=course_id,
        symptom_code=symptom_code,
        symptom_text=symptom_text,
        severity=severity,
        started_on=started_on,
        today=today,
    )
    end_day = _ended(ongoing, ended_on, started_on, today)
    event = AdverseEvent(
        id=uuid.uuid4(),
        created_at=now,
        patient_id=actor.patient_id,
        case_id=case_row.id,
        course_id=course_id,
        drug_name=drug_name,
        symptom_code=symptom_code,
        symptom_text=text,
        severity=severity,
        started_on=started_on,
        ongoing=ongoing,
        ended_on=end_day,
        reported_by=actor.user_id,
        reporter_role=Role.PATIENT,
        submission_id=submission_id,
        last_therapy_change_on=last_change,
        status=AeStatus.REPORTED,
        relation=None,
        in_label=None,
        report_required=False,
        report_sent_on=None,
        doctor_note=None,
        reviewed_by=None,
        reviewed_at=None,
    )
    session.add(event)
    await session.flush()
    rules = knowledge.adverse_event_rules
    level = rules.severity(severity).alert
    label = rules.symptom_label(symptom_code) or symptom_code
    if level is not None:
        await monitoring.raise_alert(
            session,
            patient_id=actor.patient_id,
            case_id=case_row.id,
            level=AlertLevel(level),
            source="adverse_event",
            source_id=event.id,
            reason=f"НЯ: {label}{f' ({drug_name})' if drug_name else ''} — «{rules.severity(severity).label}»",
            details={"adverse_event_id": str(event.id), "severity": severity},
            dedup_key=f"adverse_event:{event.id}",
            knowledge_version=knowledge.version,
            now=now,
        )
    doctor_user = await session.scalar(
        select(Doctor.user_id)
        .join(Patient, Patient.treating_doctor_id == Doctor.id)
        .where(Patient.id == actor.patient_id)
    )
    if doctor_user is not None:
        await notifications.notify(
            session,
            doctor_user,
            "adverse_event",
            {"adverse_event_id": str(event.id), "patient_id": str(actor.patient_id), "severity": severity},
            now=now,
            patient_id=actor.patient_id,
        )
    await audit.record(
        session,
        actor,
        "adverse_event.reported",
        entity_type="adverse_event",
        entity_id=event.id,
        patient_id=actor.patient_id,
        details={"symptom": symptom_code, "severity": severity},
        now=now,
    )
    return event


def _ended(ongoing: bool, ended_on: date | None, started_on: date, today: date) -> date | None:
    """A finished symptom needs its end date (between the start and today); an ongoing one has none."""
    if ongoing:
        return None
    if ended_on is None or not started_on <= ended_on <= today:
        raise _problem("Укажите дату окончания — между началом и сегодняшним днём", "ended_on")
    return ended_on


def _assess(
    event: AdverseEvent,
    *,
    status: AeStatus,
    relation: DrugRelation | None,
    in_label: Tristate | None,
    report_required: bool,
    note: str | None,
    actor: Actor,
    now: datetime,
) -> None:
    if status is AeStatus.REPORTED:
        raise _problem("Укажите решение: зафиксировать или отклонить", "status")
    clean = (note or "").strip() or None
    if status is AeStatus.DISMISSED and not clean:
        raise _problem("Укажите, почему это не НЯ", "note")
    if status is AeStatus.CONFIRMED and relation is None:
        raise _problem("Оцените связь с препаратом", "relation")
    if event.report_sent_on is not None and (status is not AeStatus.CONFIRMED or not report_required):
        raise InvalidStateError("Извещение в Росздравнадзор уже отправлено: снять НЯ или обязанность извещения нельзя")
    event.status, event.relation, event.in_label = status, relation, in_label
    event.report_required = report_required and status is AeStatus.CONFIRMED
    event.doctor_note, event.reviewed_by, event.reviewed_at = clean, actor.user_id, now


async def record(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    *,
    submission_id: uuid.UUID,
    course_id: uuid.UUID | None,
    symptom_code: str,
    symptom_text: str | None,
    severity: str,
    started_on: date,
    ongoing: bool,
    ended_on: date | None = None,
    relation: DrugRelation,
    in_label: Tristate | None,
    report_required: bool,
    note: str | None,
    now: datetime,
) -> AdverseEvent:
    """The treating doctor records an adverse event directly (confirmed at once). Idempotent per
    ``submission_id``."""
    await require_treating_doctor(session, actor, patient_id)
    existing = await session.scalar(
        select(AdverseEvent).where(
            AdverseEvent.reported_by == actor.user_id, AdverseEvent.submission_id == submission_id
        )
    )
    if existing is not None:
        if existing.patient_id != patient_id:
            raise InvalidStateError("Этот идентификатор отправки уже использован для другого пациента")
        return existing
    case_row = await _active_case(session, patient_id)
    today = local_date(now, await patient_timezone(session, patient_id))
    drug_name, text, last_change = await _validated(
        session,
        knowledge,
        case_row,
        course_id=course_id,
        symptom_code=symptom_code,
        symptom_text=symptom_text,
        severity=severity,
        started_on=started_on,
        today=today,
    )
    end_day = _ended(ongoing, ended_on, started_on, today)
    event = AdverseEvent(
        id=uuid.uuid4(),
        created_at=now,
        patient_id=patient_id,
        case_id=case_row.id,
        course_id=course_id,
        drug_name=drug_name,
        symptom_code=symptom_code,
        symptom_text=text,
        severity=severity,
        started_on=started_on,
        ongoing=ongoing,
        ended_on=end_day,
        reported_by=actor.user_id,
        reporter_role=Role.DOCTOR,
        submission_id=submission_id,
        last_therapy_change_on=last_change,
        status=AeStatus.REPORTED,
        relation=None,
        in_label=None,
        report_required=False,
        report_sent_on=None,
        doctor_note=None,
        reviewed_by=None,
        reviewed_at=None,
    )
    _assess(
        event,
        status=AeStatus.CONFIRMED,
        relation=relation,
        in_label=in_label,
        report_required=report_required,
        note=note,
        actor=actor,
        now=now,
    )
    session.add(event)
    await session.flush()
    await audit.record(
        session,
        actor,
        "adverse_event.recorded",
        entity_type="adverse_event",
        entity_id=event.id,
        patient_id=patient_id,
        details={
            "symptom": symptom_code,
            "severity": severity,
            "relation": relation.value,
            "report_required": event.report_required,
        },
        now=now,
    )
    return event


async def _doctor_event(session: AsyncSession, actor: Actor, event_id: uuid.UUID) -> AdverseEvent:
    event = await session.get(AdverseEvent, event_id)
    if event is None:
        raise NotFoundError("НЯ не найдено")
    try:
        await require_treating_doctor(session, actor, event.patient_id)
    except NotFoundError:
        raise NotFoundError("НЯ не найдено") from None
    locked = await session.get(AdverseEvent, event_id, with_for_update=True, populate_existing=True)
    assert locked is not None
    return locked


async def review(
    session: AsyncSession,
    actor: Actor,
    event_id: uuid.UUID,
    *,
    status: AeStatus,
    relation: DrugRelation | None,
    in_label: Tristate | None,
    report_required: bool,
    note: str | None,
    now: datetime,
) -> AdverseEvent:
    """The doctor confirms («Зафиксировать НЯ», causality required) or dismisses (reason required) a
    report; a later review replaces the earlier one (both audited)."""
    event = await _doctor_event(session, actor, event_id)
    previous = event.status
    _assess(
        event,
        status=status,
        relation=relation,
        in_label=in_label,
        report_required=report_required,
        note=note,
        actor=actor,
        now=now,
    )
    await audit.record(
        session,
        actor,
        "adverse_event.reviewed",
        entity_type="adverse_event",
        entity_id=event.id,
        patient_id=event.patient_id,
        details={
            "status": status.value,
            "previous": previous.value,
            "relation": relation.value if relation else None,
            "report_required": event.report_required,
        },
        now=now,
    )
    return event


async def mark_report_sent(
    session: AsyncSession, actor: Actor, event_id: uuid.UUID, *, sent_on: date, now: datetime
) -> AdverseEvent:
    """The doctor notes that the Росздравнадзор report was sent (outside the system).

    Raises:
        InvalidStateError: no report is required, or it is already marked as sent.
        InvalidInputError: the date is before the event started or in the future.
    """
    event = await _doctor_event(session, actor, event_id)
    if not event.report_required:
        raise InvalidStateError("Для этого НЯ извещение не требуется")
    if event.report_sent_on is not None:
        raise InvalidStateError(f"Извещение уже отмечено как отправленное {event.report_sent_on:%d.%m.%Y}")
    today = local_date(now, await patient_timezone(session, event.patient_id))
    if not (event.started_on or event.created_at.date()) <= sent_on <= today:
        raise _problem("Дата отправки — не раньше начала НЯ и не позже сегодняшнего дня", "sent_on")
    event.report_sent_on = sent_on
    await audit.record(
        session,
        actor,
        "adverse_event.report_sent",
        entity_type="adverse_event",
        entity_id=event.id,
        patient_id=event.patient_id,
        details={"sent_on": sent_on.isoformat()},
        now=now,
    )
    return event


async def end(
    session: AsyncSession, actor: Actor, event_id: uuid.UUID, *, ended_on: date, now: datetime
) -> AdverseEvent:
    """The patient says the symptom has passed."""
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    found = await session.get(AdverseEvent, event_id)
    if found is None or found.patient_id != actor.patient_id:
        raise NotFoundError("НЯ не найдено")
    event = await session.get(AdverseEvent, event_id, with_for_update=True, populate_existing=True)
    assert event is not None
    today = local_date(now, await patient_timezone(session, actor.patient_id))
    if (event.started_on is not None and ended_on < event.started_on) or ended_on > today:
        raise _problem("Дата окончания — между началом и сегодняшним днём", "ended_on")
    event.ongoing, event.ended_on = False, ended_on
    await audit.record(
        session,
        actor,
        "adverse_event.ended",
        entity_type="adverse_event",
        entity_id=event.id,
        patient_id=event.patient_id,
        details={"ended_on": ended_on.isoformat()},
        now=now,
    )
    return event


_PENDING_REPORT = AdverseEvent.report_required.is_(True) & AdverseEvent.report_sent_on.is_(None)


async def for_doctor(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, *, offset: int = 0, limit: int = 50
) -> list[AdverseEvent]:
    """Events of the patient: waiting for review first, then reports still to send, then newest."""
    await require_treating_doctor(session, actor, patient_id)
    order = case((AdverseEvent.status == AeStatus.REPORTED, 0), (_PENDING_REPORT, 1), else_=2)
    rows = await session.scalars(
        select(AdverseEvent)
        .where(AdverseEvent.patient_id == patient_id)
        .order_by(order, AdverseEvent.created_at.desc(), AdverseEvent.id)
        .offset(offset)
        .limit(limit)
    )
    return list(rows.all())


async def doctor_todo(session: AsyncSession, actor: Actor) -> list[AdverseEvent]:
    """The doctor's reminder across all their patients (spec §9 «система напоминает»): reports waiting
    for review and confirmed events whose Росздравнадзор report is not yet marked as sent."""
    if not actor.is_doctor or actor.doctor_id is None:
        raise PermissionDeniedError("Действие доступно только врачу")
    rows = await session.scalars(
        select(AdverseEvent)
        .join(Patient, Patient.id == AdverseEvent.patient_id)
        .where(
            Patient.treating_doctor_id == actor.doctor_id, (AdverseEvent.status == AeStatus.REPORTED) | _PENDING_REPORT
        )
        .order_by(AdverseEvent.created_at)
        .limit(200)
    )
    return list(rows.all())


async def for_patient(session: AsyncSession, actor: Actor, *, offset: int = 0, limit: int = 50) -> list[AdverseEvent]:
    """The patient's own reports (and those the doctor recorded), newest first."""
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    rows = await session.scalars(
        select(AdverseEvent)
        .where(AdverseEvent.patient_id == actor.patient_id)
        .order_by(AdverseEvent.created_at.desc(), AdverseEvent.id)
        .offset(offset)
        .limit(limit)
    )
    return list(rows.all())
