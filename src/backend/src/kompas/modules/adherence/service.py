"""Adherence use cases: the patient's doses and marks, the doctor's calendar and summary, the reminder
rule, and two worker jobs (send due reminders; raise a signal for a run of skipped days).

Expected doses are always recomputed from the confirmed prescription versions, so a reminder or a mark
can only refer to a dose that the current (or, for past doses, the then-current) version prescribes —
the "re-check before sending" of A15 is built in.
"""

import logging
import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, Integer, Uuid, exists, literal, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kompas.core.actor import Actor
from kompas.core.clock import local_date
from kompas.core.db import transaction
from kompas.core.errors import InvalidInputError, InvalidStateError, NotFoundError, PermissionDeniedError, Problem
from kompas.json_input import parse_regimen
from kompas.modules.adherence.domain.doses import (
    AdherenceSummary,
    CourseRegimen,
    DoseState,
    ExpectedDose,
    Mark,
    MarkStatus,
    PrescriptionWindow,
    SkipStreak,
    dose_state,
    expected_doses,
    mark_problem,
    reminder_times,
    skip_streaks,
    summarize,
)
from kompas.modules.adherence.models import DoseMark, DoseMarkSubmission, ReminderRule, ReminderSent
from kompas.modules.audit import service as audit
from kompas.modules.cases.models import CaseStatus, MedicalCase
from kompas.modules.cases.service import patient_timezone, require_treating_doctor
from kompas.modules.identity.models import Patient
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.care_rules import ReminderDefaults
from kompas.modules.monitoring import service as monitoring
from kompas.modules.monitoring.domain.alerts import AlertStatus
from kompas.modules.monitoring.models import AlertLevel, AlertRow
from kompas.modules.notifications import service as notifications
from kompas.modules.therapy.domain.basket import DailySchedule
from kompas.modules.therapy.models import Prescription, PrescriptionItem

_log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class DoseView:
    dose: ExpectedDose
    mark: DoseMark | None
    state: DoseState
    problem: str | None  # why it can't be marked now (None = can be marked)


@dataclass(frozen=True, slots=True)
class CourseReport:
    course_id: str
    name: str
    summary: AdherenceSummary


@dataclass(frozen=True, slots=True)
class AdherenceReport:
    """The doctor's view of a period."""

    since: date
    until: date
    summary: AdherenceSummary
    courses: tuple[CourseReport, ...]
    doses: tuple[DoseView, ...]
    streaks: tuple[SkipStreak, ...]


def _patient(actor: Actor) -> uuid.UUID:
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    return actor.patient_id


async def _active_case(session: AsyncSession, patient_id: uuid.UUID) -> MedicalCase | None:
    return await session.scalar(
        select(MedicalCase).where(
            MedicalCase.patient_id == patient_id, MedicalCase.status == CaseStatus.ACTIVE, MedicalCase.demo.is_(False)
        )
    )


async def prescription_windows(session: AsyncSession, case_id: uuid.UUID) -> list[PrescriptionWindow]:
    """Every confirmed version of the case with the period it was in force."""
    versions = list(
        (
            await session.scalars(
                select(Prescription).where(Prescription.case_id == case_id).order_by(Prescription.version)
            )
        ).all()
    )
    if not versions:
        return []
    items: dict[uuid.UUID, list[PrescriptionItem]] = {v.id: [] for v in versions}
    for item in await session.scalars(select(PrescriptionItem).where(PrescriptionItem.prescription_id.in_(items))):
        items[item.prescription_id].append(item)
    windows = []
    for i, version in enumerate(versions):
        until = versions[i + 1].confirmed_at if i + 1 < len(versions) else None
        courses = tuple(
            CourseRegimen(
                str(item.course_id),
                item.trade_name,
                parse_regimen(item.regimen, "regimen") if item.regimen is not None else None,
            )
            for item in items[version.id]
        )
        windows.append(PrescriptionWindow(str(version.id), version.confirmed_at, until, courses))
    return windows


async def _marks(
    session: AsyncSession, patient_id: uuid.UUID, doses: Sequence[ExpectedDose]
) -> dict[tuple[str, datetime], DoseMark]:
    if not doses:
        return {}
    rows = await session.scalars(
        select(DoseMark).where(
            DoseMark.patient_id == patient_id,
            DoseMark.scheduled_at >= min(d.scheduled_at for d in doses),
            DoseMark.scheduled_at <= max(d.scheduled_at for d in doses),
        )
    )
    return {(str(m.course_id), m.scheduled_at): m for m in rows}


def _domain_mark(row: DoseMark | None) -> Mark | None:
    return Mark(row.status, row.taken_at, row.reason) if row else None


async def _views(
    session: AsyncSession,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    case_id: uuid.UUID,
    since: date,
    until: date,
    now: datetime,
) -> list[DoseView]:
    tz = await patient_timezone(session, patient_id)
    doses = expected_doses(await prescription_windows(session, case_id), tz, since, until)
    marks = await _marks(session, patient_id, doses)
    rules = knowledge.adherence_rules
    views = []
    for dose in doses:
        row = marks.get((dose.course_id, dose.scheduled_at))
        views.append(
            DoseView(dose, row, dose_state(dose, _domain_mark(row), now, rules), mark_problem(dose, now, rules))
        )
    return views


async def my_doses(
    session: AsyncSession, actor: Actor, knowledge: Knowledge, *, since: date, until: date, now: datetime
) -> list[DoseView]:
    """The patient's expected doses (with marks and states) for local days ``since..until``."""
    patient_id = _patient(actor)
    if (until - since).days > 62:
        raise InvalidInputError(
            "Период не больше двух месяцев", (Problem("invalid_input", "Слишком длинный период", "until"),)
        )
    case = await _active_case(session, patient_id)
    if case is None:
        return []
    return await _views(session, knowledge, patient_id, case.id, since, until, now)


async def mark_dose(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    *,
    submission_id: uuid.UUID,
    course_id: uuid.UUID,
    scheduled_at: datetime,
    status: MarkStatus,
    taken_at: datetime | None,
    reason: str | None,
    now: datetime,
) -> DoseView:
    """Patient marks one dose «Принял» (actual time, defaults to now) or «Пропустил» (reason required).
    A later mark of the same dose replaces the earlier one (both audited). Repeating a
    ``submission_id`` returns the stored outcome.

    Raises:
        NotFoundError: the dose is not in the prescription. InvalidInputError: outside the marking
        window, missing/unknown skip reason, or a taken time in the future.
    """
    patient_id = _patient(actor)
    rules = knowledge.adherence_rules
    case = await _active_case(session, patient_id)
    if case is None:
        raise NotFoundError("Нет активного назначения")
    tz = await patient_timezone(session, patient_id)

    async def find(course: uuid.UUID, at: datetime) -> DoseView | None:
        day = local_date(at, tz)
        views = await _views(session, knowledge, patient_id, case.id, day, day, now)
        return next((v for v in views if v.dose.course_id == str(course) and v.dose.scheduled_at == at), None)

    # idempotency first: a retried submission returns the dose's current mark, never re-applies itself
    seen = await session.scalar(
        select(DoseMarkSubmission).where(
            DoseMarkSubmission.patient_id == patient_id, DoseMarkSubmission.submission_id == submission_id
        )
    )
    if seen is not None:
        stored = await session.get(DoseMark, seen.mark_id)
        assert stored is not None
        found = await find(stored.course_id, stored.scheduled_at)
        if found is None:
            raise InvalidStateError("Отметка сохранена, но этого приёма больше нет в назначении")
        return found
    view = await find(course_id, scheduled_at)
    if view is None:
        raise NotFoundError(
            "Такого приёма нет в назначении", (Problem("not_found", "Нет такого приёма", "scheduled_at"),)
        )
    if view.problem:
        raise InvalidInputError(view.problem, (Problem("mark_window", view.problem, "scheduled_at"),))
    if status is MarkStatus.SKIPPED:
        if reason not in rules.reason_codes():
            raise InvalidInputError(
                "Укажите причину пропуска", (Problem("reason_required", "Нужна причина пропуска", "reason"),)
            )
        taken_at = None
    else:
        reason = None
        taken_at = taken_at or now
        if taken_at > now + timedelta(minutes=1):
            raise InvalidInputError(
                "Время приёма в будущем", (Problem("invalid_input", "Время в будущем", "taken_at"),)
            )
    row = view.mark
    action = "dose.mark_changed" if row is not None else "dose.marked"
    if row is None:
        row = DoseMark(
            id=uuid.uuid4(),
            created_at=now,
            patient_id=patient_id,
            case_id=case.id,
            prescription_id=uuid.UUID(view.dose.prescription_id),
            course_id=course_id,
            scheduled_at=scheduled_at,
            status=status,
            taken_at=taken_at,
            reason=reason,
            submission_id=submission_id,
            updated_at=now,
        )
        session.add(row)
    else:
        row.status, row.taken_at, row.reason = status, taken_at, reason
        row.submission_id, row.updated_at = submission_id, now
    await session.flush()
    session.add(
        DoseMarkSubmission(
            id=uuid.uuid4(),
            created_at=now,
            patient_id=patient_id,
            submission_id=submission_id,
            mark_id=row.id,
            status=status,
            taken_at=taken_at,
            reason=reason,
        )
    )
    await session.flush()
    await audit.record(
        session,
        actor,
        action,
        entity_type="dose_mark",
        entity_id=row.id,
        patient_id=patient_id,
        details={
            "course_id": str(course_id),
            "scheduled_at": scheduled_at.isoformat(),
            "status": status.value,
            "reason": reason,
        },
        now=now,
    )
    state = dose_state(view.dose, _domain_mark(row), now, rules)
    return DoseView(view.dose, row, state, None)


async def report(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    *,
    since: date,
    until: date,
    now: datetime,
) -> AdherenceReport:
    """The doctor's calendar and summary (overall and per drug), plus running skip streaks."""
    await require_treating_doctor(session, actor, patient_id)
    if (until - since).days > 186:
        raise InvalidInputError(
            "Период не больше полугода", (Problem("invalid_input", "Слишком длинный период", "until"),)
        )
    case = await _active_case(session, patient_id)
    views = await _views(session, knowledge, patient_id, case.id, since, until, now) if case else []
    rules = knowledge.adherence_rules
    pairs = [(v.state, _domain_mark(v.mark)) for v in views]
    by_course: dict[str, list[DoseView]] = {}
    for v in views:
        by_course.setdefault(v.dose.course_id, []).append(v)
    courses = tuple(
        CourseReport(cid, vs[0].dose.name, summarize([(v.state, _domain_mark(v.mark)) for v in vs], rules))
        for cid, vs in by_course.items()
    )
    streaks = tuple(skip_streaks([v.dose for v in views], [v.state for v in views], rules, now))
    return AdherenceReport(since, until, summarize(pairs, rules), courses, tuple(views), streaks)


async def reminder_rule(session: AsyncSession, actor: Actor, knowledge: Knowledge) -> ReminderDefaults:
    """The patient's reminder rule (their own, or the defaults)."""
    return await _rule_of(session, _patient(actor), knowledge)


async def _rule_of(session: AsyncSession, patient_id: uuid.UUID, knowledge: Knowledge) -> ReminderDefaults:
    row = await session.get(ReminderRule, patient_id)
    if row is None:
        return knowledge.adherence_rules.reminders
    return ReminderDefaults(
        enabled=row.enabled,
        first_offset_minutes=row.first_offset_minutes,
        repeat_every_minutes=row.repeat_every_minutes,
        max_repeats=row.max_repeats,
    )


async def set_reminder_rule(
    session: AsyncSession, actor: Actor, knowledge: Knowledge, rule: ReminderDefaults, *, now: datetime
) -> ReminderDefaults:
    """Patient changes their reminder rule within the configured limits (A15 frequency limit)."""
    patient_id = _patient(actor)
    limits = knowledge.adherence_rules.reminder_limits
    problems = []
    if rule.max_repeats > limits.max_repeats:
        problems.append(Problem("invalid_input", f"Повторов не больше {limits.max_repeats}", "max_repeats"))
    if rule.repeat_every_minutes < limits.min_repeat_every_minutes:
        problems.append(
            Problem(
                "invalid_input", f"Интервал не меньше {limits.min_repeat_every_minutes} мин", "repeat_every_minutes"
            )
        )
    if problems:
        raise InvalidInputError("Настройки напоминаний вне допустимых пределов", tuple(problems))
    values = rule.model_dump() | {"updated_at": now}
    await session.execute(
        insert(ReminderRule)
        .values(patient_id=patient_id, **values)
        .on_conflict_do_update(index_elements=[ReminderRule.patient_id], set_=values)
    )
    await audit.record(
        session,
        actor,
        "reminders.updated",
        entity_type="reminder_rule",
        entity_id=patient_id,
        patient_id=patient_id,
        details=rule.model_dump(),
        now=now,
    )
    return rule


# ------------------------------------------------------------------------------------- worker jobs


async def _active_cases(session: AsyncSession) -> list[tuple[uuid.UUID, uuid.UUID, uuid.UUID | None]]:
    """(case id, patient id, patient user id) for every active case that has a prescription."""
    rows = await session.execute(
        select(MedicalCase.id, MedicalCase.patient_id, Patient.user_id)
        .join(Patient, Patient.id == MedicalCase.patient_id)
        .where(
            MedicalCase.status == CaseStatus.ACTIVE,
            MedicalCase.demo.is_(False),
            select(Prescription.id).where(Prescription.case_id == MedicalCase.id).exists(),
        )
    )
    return [(c, p, u) for c, p, u in rows.all()]


async def send_due_reminders(factory: async_sessionmaker[AsyncSession], knowledge: Knowledge, *, now: datetime) -> int:
    """Send every reminder that is due now and not sent yet, for unmarked doses only. Each send is
    recorded first (unique per dose and attempt), so concurrent workers never double-send; reminders
    older than ``send_grace_minutes`` are dropped instead of flooding after downtime. Returns the
    number sent."""
    grace = timedelta(minutes=knowledge.adherence_rules.reminder_limits.send_grace_minutes)
    async with factory() as session:
        cases = await _active_cases(session)
    sent = 0
    for case_id, patient_id, user_id in cases:
        if user_id is None:
            continue
        try:
            sent += await _remind_case(factory, knowledge, case_id, patient_id, user_id, grace=grace, now=now)
        except Exception:  # one broken case (bad stored data) must not stop reminders for everyone
            _log.exception("reminders failed for case %s", case_id)
    return sent


def _still_prescribed(windows: Sequence[PrescriptionWindow], dose: ExpectedDose) -> bool:
    """A15 re-check: the dose (course, local time, amount) is in the version in force *now*, so a drug
    the doctor stopped or re-dosed after the dose's time gets no more reminders."""
    if not windows or windows[-1].until is not None:
        return False
    for course in windows[-1].courses:
        regimen = course.regimen
        if course.course_id != dose.course_id or regimen is None or not isinstance(regimen.schedule, DailySchedule):
            continue
        if regimen.start <= dose.day <= regimen.end and any(
            i.at == dose.at and i.amount_mg == dose.amount_mg for i in regimen.schedule.intakes
        ):
            return True
    return False


async def _remind_case(
    factory: async_sessionmaker[AsyncSession],
    knowledge: Knowledge,
    case_id: uuid.UUID,
    patient_id: uuid.UUID,
    user_id: uuid.UUID,
    *,
    grace: timedelta,
    now: datetime,
) -> int:
    sent = 0
    async with transaction(factory) as session:
        rule = await _rule_of(session, patient_id, knowledge)
        if not rule.enabled:
            return 0
        tz: ZoneInfo = await patient_timezone(session, patient_id)
        today = local_date(now, tz)
        windows = await prescription_windows(session, case_id)
        for view in await _views(session, knowledge, patient_id, case_id, today - timedelta(days=1), today, now):
            if view.mark is not None or not _still_prescribed(windows, view.dose):
                continue  # a mark cancels the remaining reminders; so does a changed prescription
            for attempt, send_at in reminder_times(view.dose, rule):
                if not (now - grace <= send_at <= now):
                    continue
                course = uuid.UUID(view.dose.course_id)
                scheduled = view.dose.scheduled_at
                unmarked = ~exists().where(DoseMark.course_id == course, DoseMark.scheduled_at == scheduled)
                inserted = await session.scalar(
                    insert(ReminderSent)
                    .from_select(
                        ["id", "patient_id", "course_id", "scheduled_at", "attempt", "sent_at"],
                        select(
                            literal(uuid.uuid4(), Uuid),
                            literal(patient_id, Uuid),
                            literal(course, Uuid),
                            literal(scheduled, DateTime(timezone=True)),
                            literal(attempt, Integer),
                            literal(now, DateTime(timezone=True)),
                        ).where(unmarked),  # re-checked in the same statement: no reminder after a mark
                    )
                    .on_conflict_do_nothing(constraint="uq_reminder_sent_attempt")
                    .returning(ReminderSent.id)
                )
                if inserted is None:
                    continue
                dose = view.dose
                await notifications.notify(
                    session,
                    user_id,
                    "dose_reminder",
                    {
                        "course_id": dose.course_id,
                        "scheduled_at": dose.scheduled_at.isoformat(),
                        "name": dose.name,
                        "at": dose.at.strftime("%H:%M"),
                        "amount_mg": float(dose.amount_mg),
                        "attempt": attempt,
                        "last": attempt == rule.max_repeats,
                    },
                    now=now,
                    patient_id=patient_id,
                )
                sent += 1
    return sent


STREAK_LOOKBACK_DAYS = 62


async def check_skip_streaks(factory: async_sessionmaker[AsyncSession], knowledge: Knowledge, *, now: datetime) -> int:
    """Raise one «Высокий» signal per running streak of skipped/unmarked days (spec §9: three days by
    default). The streak is measured back to its real first day (up to two months) and deduplicated by
    it; while an adherence signal for the drug is still open, no new one is raised. Returns the number
    of running streaks found."""
    found = 0
    async with factory() as session:
        cases = await _active_cases(session)
    for case_id, patient_id, _ in cases:
        try:
            found += await _streaks_case(factory, knowledge, case_id, patient_id, now=now)
        except Exception:  # one broken case must not stop the check for everyone
            _log.exception("skip-streak check failed for case %s", case_id)
    return found


async def _streaks_case(
    factory: async_sessionmaker[AsyncSession],
    knowledge: Knowledge,
    case_id: uuid.UUID,
    patient_id: uuid.UUID,
    *,
    now: datetime,
) -> int:
    rules = knowledge.adherence_rules
    found = 0
    async with transaction(factory) as session:
        tz = await patient_timezone(session, patient_id)
        today = local_date(now, tz)
        since = today - timedelta(days=STREAK_LOOKBACK_DAYS)
        views = await _views(session, knowledge, patient_id, case_id, since, today, now)
        for streak in skip_streaks([v.dose for v in views], [v.state for v in views], rules, now):
            found += 1
            open_signal = await session.scalar(
                select(AlertRow.id).where(
                    AlertRow.source == "adherence",
                    AlertRow.source_id == uuid.UUID(streak.course_id),
                    AlertRow.status != AlertStatus.HANDLED,
                )
            )
            if open_signal is not None:
                continue  # the doctor already has an open signal for this drug
            first = streak.last_day - timedelta(days=streak.days - 1)
            await monitoring.raise_alert(
                session,
                patient_id=patient_id,
                case_id=case_id,
                level=AlertLevel.HIGH,
                source="adherence",
                source_id=uuid.UUID(streak.course_id),
                reason=f"{streak.name}: пропуски или нет отметок {streak.days} дн. подряд (с {first:%d.%m})",
                details={"course_id": streak.course_id, "first_day": first.isoformat(), "days": streak.days},
                dedup_key=f"adherence:{streak.course_id}:{first.isoformat()}",
                knowledge_version=knowledge.version,
                now=now,
            )
    return found
