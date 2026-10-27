"""Assessment use cases.

A submission is **one transaction**: store the raw response → evaluate critical item rules and raise
alerts referencing the response (D-030) → score. A doctor's own rating raises no alerts: the doctor is
with the patient and sees the critical items in the result (D-119). If scoring fails validation, the response is kept
as ``rejected`` and the function returns the problems instead of raising, so the alert commits; the
router turns the problems into a 422. Patients may submit only patient scales, doctors only
clinician scales. Results are doctor-only: a patient sees that the answers were saved, never a result
(D-109).
"""

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.core.clock import local_date
from kompas.core.errors import InvalidInputError, KompasError, NotFoundError, PermissionDeniedError, Problem
from kompas.core.jsonable import to_jsonable
from kompas.modules.assessments.domain.dynamics import Measurement, SeriesKey
from kompas.modules.assessments.domain.scoring import CriticalHit, item_critical_hits, score, validate_answers
from kompas.modules.assessments.models import Assignment, AssignmentMode, Response, ResponseStatus, Result
from kompas.modules.audit import service as audit
from kompas.modules.cases.models import CaseStatus, MedicalCase
from kompas.modules.cases.service import case_for_actor, patient_timezone, require_treating_doctor
from kompas.modules.identity.service import ensure_patient_access
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.scales import FilledBy, ScaleDefinition
from kompas.modules.monitoring import service as monitoring
from kompas.modules.monitoring.models import AlertLevel, AlertRow
from kompas.modules.referrals import service as referrals


def _scale(knowledge: Knowledge, code: str) -> ScaleDefinition:
    definition = knowledge.scales.get(code)
    if definition is None:
        raise NotFoundError(f"Шкала {code!r} не найдена", (Problem("not_found", "Неизвестная шкала", "scale_code"),))
    return definition


async def patient_case(session: AsyncSession, actor: Actor) -> MedicalCase:
    """The case a patient's self-report goes to: the active one, else the most recent one (a closed
    case still stores the answers as rejected, so critical answers alert). NotFound if none."""
    patient_id = _require_patient(actor)
    case = await session.scalar(
        select(MedicalCase)
        .where(MedicalCase.patient_id == patient_id, MedicalCase.demo.is_(False))
        .order_by((MedicalCase.status == CaseStatus.ACTIVE).desc(), MedicalCase.created_at.desc())
        .limit(1)
    )
    if case is None:
        raise NotFoundError("У пациента нет случая")
    return case


def _require_patient(actor: Actor) -> uuid.UUID:
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    return actor.patient_id


def _hit_key(response_id: uuid.UUID, hit: CriticalHit) -> str:
    if hit.item is not None:
        return f"response:{response_id}:item:{hit.item}"
    if hit.band_code is None:
        rule = hashlib.sha256((hit.note or "").encode()).hexdigest()[:16]
        return f"response:{response_id}:aggregate:{rule}"
    return f"response:{response_id}:band:{hit.band_code}"


def _hit_reason(definition: ScaleDefinition, hit: CriticalHit) -> str:
    where = (
        f"пункт {hit.item} = {hit.value}"
        if hit.item is not None
        else f"результат «{hit.band_code}»"
        if hit.band_code is not None
        else "совокупность ответов"
    )
    return f"{definition.name}: критический ответ ({where})" + (f" — {hit.note}" if hit.note else "")


async def _existing(session: AsyncSession, response: Response) -> "Submission":
    result = await session.scalar(select(Result).where(Result.response_id == response.id))
    alert_ids = (await session.scalars(select(AlertRow.id).where(AlertRow.source_id == response.id))).all()
    problems = tuple(Problem(p["code"], p["message"], p.get("pointer")) for p in response.problems)
    return Submission(response, result, problems, tuple(alert_ids))


def _band_level(result: Result) -> int | None:
    """Severity 0–4 of a result (the worst subscale for subscale scales), for doctor-side rules."""
    if result.band is not None:
        return int(result.band["level"])
    levels = [int(s["band"]["level"]) for s in result.subscores if s.get("band") is not None]
    return max(levels) if levels else None


@dataclass(frozen=True, slots=True)
class Submission:
    """Outcome of ``submit``: the stored response, the result (None if rejected), validation
    problems, and ids of alerts raised."""

    response: Response
    result: Result | None
    problems: tuple[Problem, ...]
    alert_ids: tuple[uuid.UUID, ...]


@dataclass(frozen=True, slots=True)
class PatientResponseItem:
    """One questionnaire in the patient's history: scores and key-based interpretations."""

    response_id: uuid.UUID
    scale_name: str
    taken_on: date
    scale_code: str
    total: float | None
    band: dict[str, Any] | None
    subscores: list[dict[str, Any]]


async def assign(
    session: AsyncSession,
    actor: Actor,
    case_id: uuid.UUID,
    knowledge: Knowledge,
    *,
    scale_code: str,
    mode: AssignmentMode,
    every_days: int | None,
    starts_on: date,
    due_days: int,
    now: datetime,
) -> Assignment:
    """Treating doctor assigns a patient scale in an active case; audited.

    Raises:
        NotFoundError: unknown scale. InvalidInputError: clinician scale, or REPEAT without every_days.
    """
    case = await case_for_actor(session, actor, case_id, active=True)
    await require_treating_doctor(session, actor, case.patient_id)
    definition = _scale(knowledge, scale_code)
    if definition.filled_by is FilledBy.CLINICIAN:
        raise InvalidInputError(
            "Пациенту можно назначить только шкалу самоотчёта",
            (Problem("clinician_scale", "Шкала заполняется врачом", "scale_code"),),
        )
    if mode is AssignmentMode.REPEAT and not every_days:
        raise InvalidInputError(
            "Для повторяющегося назначения нужен интервал", (Problem("invalid_input", "Нужен интервал", "every_days"),)
        )
    if due_days <= 0 or (every_days is not None and every_days <= 0):
        raise InvalidInputError("Сроки должны быть больше нуля", (Problem("invalid_input", "Срок ≤ 0", "due_days"),))
    assignment = Assignment(
        id=uuid.uuid4(),
        created_at=now,
        case_id=case.id,
        scale_code=definition.code,
        mode=mode,
        every_days=every_days if mode is AssignmentMode.REPEAT else None,
        starts_on=starts_on,
        due_days=due_days,
        active=True,
        created_by=actor.user_id,
    )
    session.add(assignment)
    await session.flush()
    await audit.record(
        session,
        actor,
        "assignment.created",
        entity_type="assignment",
        entity_id=assignment.id,
        patient_id=case.patient_id,
        details={"scale": definition.code, "mode": mode.value},
        now=now,
    )
    return assignment


async def due_assignments(session: AsyncSession, actor: Actor, *, today: date) -> list[Assignment]:
    """Patient: what to fill now (including overdue)."""
    patient_id = _require_patient(actor)
    tz = await patient_timezone(session, patient_id)
    rows = (
        await session.scalars(
            select(Assignment)
            .join(MedicalCase, MedicalCase.id == Assignment.case_id)
            .where(
                MedicalCase.patient_id == patient_id,
                MedicalCase.status == "active",
                Assignment.active.is_(True),
                Assignment.starts_on <= today,
            )
            .order_by(Assignment.starts_on)
        )
    ).all()
    due: list[Assignment] = []
    for assignment in rows:
        last = await session.scalar(
            select(func.max(Response.taken_at)).where(
                Response.assignment_id == assignment.id, Response.status == ResponseStatus.COMPLETE
            )
        )
        if assignment.mode is AssignmentMode.ONCE:
            if last is None:
                due.append(assignment)
            continue
        assert assignment.every_days is not None
        if last is None or local_date(last, tz) + timedelta(days=assignment.every_days) <= today:
            due.append(assignment)
    return due


async def submit(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    *,
    submission_id: uuid.UUID,
    case_id: uuid.UUID,
    scale_code: str,
    answers: Mapping[int | str, int],
    assignment_id: uuid.UUID | None,
    taken_at: datetime | None,
    now: datetime,
) -> Submission:
    """Store, alert, score (see module docstring). ``taken_at`` is ignored for patients (server
    ``now``) and must not be in the future for doctors. A repeated ``submission_id`` returns the
    stored submission unchanged (idempotent retry). Audited.

    Raises:
        NotFoundError: unknown scale or no access. PermissionDeniedError: role / scale mismatch.
    """
    definition = _scale(knowledge, scale_code)
    existing = await session.scalar(
        select(Response).where(Response.filled_by_user == actor.user_id, Response.submission_id == submission_id)
    )
    if existing is not None:
        return await _existing(session, existing)
    case = await case_for_actor(session, actor, case_id)
    # Input problems below never prevent storing the answers: critical answers must always alert (D-030).
    problems: list[Problem] = []
    if case.status is not CaseStatus.ACTIVE:
        problems.append(Problem("case_closed", "Случай закрыт — ответы сохранены, но не оценены", "case_id"))
    if actor.is_patient:
        if definition.filled_by is FilledBy.CLINICIAN:
            raise PermissionDeniedError("Эту шкалу заполняет врач")
        taken = now
    else:
        await require_treating_doctor(session, actor, case.patient_id)
        # A treating doctor may enter a self-report together with the patient at a visit.
        # The respondent type remains in the definition; the entering user is audited.
        taken = taken_at or now
        if taken > now:
            problems.append(Problem("invalid_input", "Дата оценки не может быть в будущем", "taken_at"))
            taken = now
    if assignment_id is not None:
        assignment = await session.get(Assignment, assignment_id)
        if assignment is None or assignment.case_id != case.id or assignment.scale_code != definition.code:
            problems.append(Problem("assignment_mismatch", "Назначение не найдено или устарело", "assignment_id"))
            assignment_id = None
    try:
        validate_answers(definition, answers)
    except KompasError as exc:
        problems.extend(exc.details or (Problem(exc.code, exc.message),))
    response = Response(
        id=uuid.uuid4(),
        created_at=now,
        submission_id=submission_id,
        case_id=case.id,
        assignment_id=assignment_id,
        scale_code=definition.code,
        scale_version=definition.version,
        filled_by=FilledBy.PATIENT if actor.is_patient else FilledBy.CLINICIAN,
        filled_by_user=actor.user_id,
        taken_at=taken,
        answers={str(k): int(v) for k, v in answers.items()},
        status=ResponseStatus.REJECTED if problems else ResponseStatus.COMPLETE,
        problems=[to_jsonable(p) for p in problems],
    )
    session.add(response)
    await session.flush()

    hits: dict[str, CriticalHit] = {_hit_key(response.id, h): h for h in item_critical_hits(definition, answers)}
    result_row: Result | None = None
    if not problems:
        scored = score(definition, answers)
        hits |= {_hit_key(response.id, h): h for h in scored.critical_hits}
        result_row = Result(
            id=uuid.uuid4(),
            created_at=now,
            response_id=response.id,
            total=scored.total,
            band=to_jsonable(scored.band) if scored.band is not None else None,
            subscores=[to_jsonable(s) for s in scored.subscores],
            critical_hits=[to_jsonable(h) for h in scored.critical_hits],
            explanation=list(scored.explanation),
        )
        session.add(result_row)
    alert_ids = []
    for key, hit in (hits if actor.is_patient else {}).items():  # a doctor rating alerts no one (D-119)
        alert = await monitoring.raise_alert(
            session,
            patient_id=case.patient_id,
            case_id=case.id,
            level=AlertLevel.URGENT,
            scenario=hit.scenario or knowledge.triage_rules.urgent_scenarios.questionnaire,
            source=f"scale:{definition.code}",
            source_id=response.id,
            reason=_hit_reason(definition, hit),
            details=to_jsonable(hit),
            dedup_key=key,
            knowledge_version=knowledge.version,
            now=now,
        )
        alert_ids.append(alert.id)
    if case.status is CaseStatus.ACTIVE:
        await referrals.recommend_from_rules(
            session,
            knowledge,
            patient_id=case.patient_id,
            case_id=case.id,
            source=f"scale:{definition.code}",
            source_id=response.id,
            scale=definition.code,
            level=_band_level(result_row) if result_row else None,
            critical=bool(hits),
            now=now,
        )
    await audit.record(
        session,
        actor,
        "response.submitted",
        entity_type="response",
        entity_id=response.id,
        patient_id=case.patient_id,
        details={"scale": definition.code, "status": response.status.value, "alerts": len(alert_ids)},
        now=now,
    )
    await session.flush()
    return Submission(response, result_row, tuple(problems), tuple(alert_ids))


async def patient_responses(
    session: AsyncSession, actor: Actor, knowledge: Knowledge, *, offset: int, limit: int
) -> tuple[list[PatientResponseItem], int]:
    """The patient's own saved questionnaires, newest first: scores and interpretations.
    Rejected submissions are not listed: the patient got the problems back and resubmits."""
    patient_id = _require_patient(actor)
    tz = await patient_timezone(session, patient_id)
    base = (
        select(Response)
        .join(MedicalCase, MedicalCase.id == Response.case_id)
        .where(
            MedicalCase.patient_id == patient_id,
            Response.status == ResponseStatus.COMPLETE,
        )
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (await session.scalars(base.order_by(Response.taken_at.desc()).offset(offset).limit(limit))).all()
    items = []
    for response in rows:
        definition = knowledge.scales.get(response.scale_code)
        result = await session.scalar(select(Result).where(Result.response_id == response.id))
        items.append(
            PatientResponseItem(
                response_id=response.id,
                scale_name=definition.name if definition else response.scale_code,
                taken_on=local_date(response.taken_at, tz),
                scale_code=response.scale_code,
                total=result.total if result else None,
                band=result.band if result else None,
                subscores=result.subscores if result else [],
            )
        )
    return items, total or 0


async def doctor_results(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, *, offset: int, limit: int
) -> tuple[list[tuple[Response, Result | None]], int]:
    """Full results for the doctor (numbers, bands, items, critical hits, rejected responses)."""
    await require_treating_doctor(session, actor, patient_id)
    base = (
        select(Response, Result)
        .outerjoin(Result, Result.response_id == Response.id)
        .join(MedicalCase, MedicalCase.id == Response.case_id)
        .where(MedicalCase.patient_id == patient_id)
    )
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = (await session.execute(base.order_by(Response.taken_at.desc()).offset(offset).limit(limit))).all()
    return [(response, result) for response, result in rows], total or 0


async def doctor_progress(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID
) -> dict[SeriesKey, list[Measurement]]:
    """All result series, accessible to the treating doctor and patient."""
    await ensure_patient_access(session, actor, patient_id)
    tz = await patient_timezone(session, patient_id)
    rows = (
        await session.execute(
            select(Response, Result)
            .join(Result, Result.response_id == Response.id)
            .join(MedicalCase, MedicalCase.id == Response.case_id)
            .where(MedicalCase.patient_id == patient_id)
            .order_by(Response.taken_at)
        )
    ).all()
    series: dict[SeriesKey, list[Measurement]] = {}
    for response, result in rows:
        taken_on = local_date(response.taken_at, tz)
        if result.total is not None:
            series.setdefault((response.scale_code, None), []).append(Measurement(taken_on, result.total))
        for sub in result.subscores:
            series.setdefault((response.scale_code, str(sub["code"])), []).append(
                Measurement(taken_on, float(sub["value"]))
            )
    return series


async def case_assignments(session: AsyncSession, actor: Actor, case_id: uuid.UUID) -> list[Assignment]:
    """Active questionnaire assignments of a case (treating doctor), oldest first."""
    case = await case_for_actor(session, actor, case_id)
    await require_treating_doctor(session, actor, case.patient_id)
    rows = await session.scalars(
        select(Assignment)
        .where(Assignment.case_id == case.id, Assignment.active.is_(True))
        .order_by(Assignment.starts_on, Assignment.created_at)
    )
    return list(rows.all())
