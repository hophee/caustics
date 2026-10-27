"""Endpoints: the patient's doses and marks, reminder settings; the doctor's adherence report."""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, Field

from kompas.api.deps import ActorDep, ClockDep, DoctorDep, KnowledgeDep, PatientDep, SessionDep
from kompas.core.clock import local_date
from kompas.modules.adherence import service
from kompas.modules.adherence.domain.doses import AdherenceSummary, MarkStatus, SkipStreak
from kompas.modules.cases.service import patient_timezone
from kompas.modules.knowledge.care_rules import ReminderDefaults

router = APIRouter(tags=["adherence"])


class MarkOut(BaseModel):
    status: MarkStatus
    taken_at: datetime | None
    reason: str | None


class DoseOut(BaseModel):
    course_id: str
    name: str
    scheduled_at: datetime
    day: date
    at: str
    amount_mg: float
    state: str  # on_time | late | skipped | unmarked | due | upcoming
    mark: MarkOut | None
    can_mark: bool
    why_not: str | None

    @classmethod
    def of(cls, v: service.DoseView) -> "DoseOut":
        d = v.dose
        mark = MarkOut(status=v.mark.status, taken_at=v.mark.taken_at, reason=v.mark.reason) if v.mark else None
        return cls(
            course_id=d.course_id,
            name=d.name,
            scheduled_at=d.scheduled_at,
            day=d.day,
            at=d.at.strftime("%H:%M"),
            amount_mg=float(d.amount_mg),
            state=v.state.value,
            mark=mark,
            can_mark=v.problem is None,
            why_not=v.problem,
        )


class MarkIn(BaseModel):
    submission_id: uuid.UUID
    course_id: uuid.UUID
    scheduled_at: AwareDatetime
    status: MarkStatus
    taken_at: AwareDatetime | None = None
    reason: str | None = None


class ReminderIn(BaseModel):
    enabled: bool
    first_offset_minutes: int = Field(ge=-120, le=120)
    repeat_every_minutes: int = Field(ge=5)
    max_repeats: int = Field(ge=0)


class SummaryOut(BaseModel):
    expected: int
    on_time: int
    late: int
    skipped: int
    unmarked: int
    percent: float | None
    band: str | None
    skip_reasons: dict[str, int]

    @classmethod
    def of(cls, s: AdherenceSummary) -> "SummaryOut":
        return cls(
            expected=s.expected,
            on_time=s.on_time,
            late=s.late,
            skipped=s.skipped,
            unmarked=s.unmarked,
            percent=s.percent,
            band=s.band.value if s.band else None,
            skip_reasons=dict(s.skip_reasons),
        )


class StreakOut(BaseModel):
    course_id: str
    name: str
    last_day: date
    days: int

    @classmethod
    def of(cls, s: SkipStreak) -> "StreakOut":
        return cls(course_id=s.course_id, name=s.name, last_day=s.last_day, days=s.days)


@router.get("/adherence/options")
async def get_options(knowledge: KnowledgeDep, _actor: ActorDep) -> dict[str, Any]:
    """Skip reasons and reminder limits for the forms."""
    rules = knowledge.adherence_rules
    return {
        "skip_reasons": [r.model_dump() for r in rules.skip_reasons],
        "reminder_limits": rules.reminder_limits.model_dump(),
        "reminder_defaults": rules.reminders.model_dump(),
        "late_after_minutes": rules.late_after_minutes,
    }


@router.get("/me/doses")
async def get_my_doses(
    actor: PatientDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
    since: date | None = Query(None),
    until: date | None = Query(None),
) -> list[DoseOut]:
    """GET /me/doses?since&until — expected doses with marks (default: today)."""
    assert actor.patient_id is not None
    today = local_date(clock.now(), await patient_timezone(session, actor.patient_id))
    views = await service.my_doses(
        session, actor, knowledge, since=since or today, until=until or since or today, now=clock.now()
    )
    return [DoseOut.of(v) for v in views]


@router.post("/me/doses/marks")
async def post_mark(
    body: MarkIn, actor: PatientDep, session: SessionDep, knowledge: KnowledgeDep, clock: ClockDep
) -> DoseOut:
    """POST /me/doses/marks — «Принял» / «Пропустил» (with reason) for one dose."""
    view = await service.mark_dose(
        session,
        actor,
        knowledge,
        submission_id=body.submission_id,
        course_id=body.course_id,
        scheduled_at=body.scheduled_at,
        status=body.status,
        taken_at=body.taken_at,
        reason=body.reason,
        now=clock.now(),
    )
    return DoseOut.of(view)


@router.get("/me/reminders")
async def get_reminders(actor: PatientDep, session: SessionDep, knowledge: KnowledgeDep) -> ReminderIn:
    """GET /me/reminders — the reminder rule shown to the patient (A15: schedule and limit visible)."""
    rule = await service.reminder_rule(session, actor, knowledge)
    return ReminderIn(**rule.model_dump())


@router.put("/me/reminders")
async def put_reminders(
    body: ReminderIn, actor: PatientDep, session: SessionDep, knowledge: KnowledgeDep, clock: ClockDep
) -> ReminderIn:
    """PUT /me/reminders — change the rule within the limits."""
    rule = await service.set_reminder_rule(
        session, actor, knowledge, ReminderDefaults(**body.model_dump()), now=clock.now()
    )
    return ReminderIn(**rule.model_dump())


@router.get("/patients/{patient_id}/adherence")
async def get_adherence(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
    since: date | None = Query(None),
    until: date | None = Query(None),
) -> dict[str, Any]:
    """GET /patients/{id}/adherence?since&until — calendar, summary overall and per drug, skip
    reasons, running skip streaks (default: the last ``summary_days`` days)."""
    today = local_date(clock.now(), await patient_timezone(session, patient_id))
    start = since or today - timedelta(days=knowledge.adherence_rules.summary_days - 1)
    rep = await service.report(
        session, actor, knowledge, patient_id, since=start, until=until or today, now=clock.now()
    )
    labels = {r.code: r.label for r in knowledge.adherence_rules.skip_reasons}
    return {
        "since": rep.since.isoformat(),
        "until": rep.until.isoformat(),
        "summary": SummaryOut.of(rep.summary).model_dump(),
        "courses": [
            {"course_id": c.course_id, "name": c.name, "summary": SummaryOut.of(c.summary).model_dump()}
            for c in rep.courses
        ],
        "doses": [DoseOut.of(v).model_dump(mode="json") for v in rep.doses],
        "streaks": [StreakOut.of(s).model_dump(mode="json") for s in rep.streaks],
        "skip_reason_labels": labels,
        "unmarked_counts_as_missed": knowledge.adherence_rules.unmarked_counts_as_missed,
    }
