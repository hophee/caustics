"""Endpoints: assignments, submissions, results, progress.

Patients can read their scores and interpretations. Item answers, critical flags and
scoring explanations remain in the doctor view.
"""

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field, StrictInt

from kompas.api.deps import ClockDep, DoctorDep, KnowledgeDep, PatientDep, SessionDep
from kompas.core.clock import local_date
from kompas.core.jsonable import to_jsonable
from kompas.modules.assessments import service
from kompas.modules.assessments.models import Assignment, AssignmentMode, Response, Result
from kompas.modules.cases.service import patient_timezone
from kompas.modules.identity.schemas import Page
from kompas.modules.knowledge.scales import ItemId

router = APIRouter(tags=["assessments"])


class AssignmentIn(BaseModel):
    scale_code: str
    mode: AssignmentMode = AssignmentMode.ONCE
    every_days: int | None = Field(default=None, ge=1)
    starts_on: date
    due_days: int = Field(default=3, ge=1)


class AssignmentOut(BaseModel):
    assignment_id: uuid.UUID
    scale_code: str
    mode: AssignmentMode
    every_days: int | None
    starts_on: date
    due_days: int

    @classmethod
    def of(cls, a: Assignment) -> "AssignmentOut":
        return cls(
            assignment_id=a.id,
            scale_code=a.scale_code,
            mode=a.mode,
            every_days=a.every_days,
            starts_on=a.starts_on,
            due_days=a.due_days,
        )


class ResponseIn(BaseModel):
    submission_id: uuid.UUID
    scale_code: str
    answers: dict[ItemId, StrictInt]
    assignment_id: uuid.UUID | None = None


class RatingIn(ResponseIn):
    taken_at: datetime | None = None


class PatientSubmissionOut(BaseModel):
    """What the patient gets back after submitting: the answers were saved. Nothing else."""

    response_id: uuid.UUID
    status: str = "saved"


class PatientResponseOut(BaseModel):
    response_id: uuid.UUID
    scale_name: str
    taken_on: date
    scale_code: str
    total: float | None
    band: dict[str, Any] | None
    subscores: list[dict[str, Any]]


class DoctorResultOut(BaseModel):
    """Everything the doctor sees for one response (numbers, bands, items, critical hits)."""

    response_id: uuid.UUID
    scale_code: str
    scale_version: int
    taken_at: datetime
    status: str
    answers: dict[str, int]
    problems: list[dict[str, Any]]
    result_id: uuid.UUID | None = None
    total: float | None = None
    band: dict[str, Any] | None = None
    subscores: list[dict[str, Any]] = []
    critical_hits: list[dict[str, Any]] = []
    explanation: list[str] = []

    @classmethod
    def of(cls, response: Response, result: Result | None) -> "DoctorResultOut":
        out = cls(
            response_id=response.id,
            scale_code=response.scale_code,
            scale_version=response.scale_version,
            taken_at=response.taken_at,
            status=response.status.value,
            answers=response.answers,
            problems=response.problems,
        )
        if result is not None:
            out.result_id = result.id
            out.total = result.total
            out.band = result.band
            out.subscores = result.subscores
            out.critical_hits = result.critical_hits
            out.explanation = result.explanation
        return out


class SeriesOut(BaseModel):
    scale_code: str
    subscale_code: str | None
    points: list[dict[str, Any]]


def _problems(submission: service.Submission) -> JSONResponse:
    """422 with the problems; returned (not raised) so the stored response and alerts commit."""
    body = {
        "error": {
            "code": "invalid_input",
            "message": "Ответы не приняты: исправьте и отправьте заново (новая отправка)",
            "details": to_jsonable(list(submission.problems)),
            "response_id": str(submission.response.id),
        }
    }
    return JSONResponse(body, status_code=422)


@router.post("/cases/{case_id}/assignments", status_code=201)
async def post_assignment(
    case_id: uuid.UUID,
    body: AssignmentIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> AssignmentOut:
    """POST /cases/{case_id}/assignments — doctor."""
    assignment = await service.assign(
        session,
        actor,
        case_id,
        knowledge,
        scale_code=body.scale_code,
        mode=body.mode,
        every_days=body.every_days,
        starts_on=body.starts_on,
        due_days=body.due_days,
        now=clock.now(),
    )
    return AssignmentOut.of(assignment)


@router.get("/cases/{case_id}/assignments")
async def get_case_assignments(case_id: uuid.UUID, actor: DoctorDep, session: SessionDep) -> list[AssignmentOut]:
    """GET /cases/{case_id}/assignments — doctor: active assignments."""
    return [AssignmentOut.of(a) for a in await service.case_assignments(session, actor, case_id)]


@router.get("/me/assignments")
async def get_due(actor: PatientDep, session: SessionDep, clock: ClockDep) -> list[AssignmentOut]:
    """GET /me/assignments — patient."""
    assert actor.patient_id is not None
    tz = await patient_timezone(session, actor.patient_id)
    rows = await service.due_assignments(session, actor, today=local_date(clock.now(), tz))
    return [AssignmentOut.of(a) for a in rows]


@router.post("/me/responses", status_code=201, response_model=PatientSubmissionOut)
async def post_my_response(
    body: ResponseIn, actor: PatientDep, session: SessionDep, knowledge: KnowledgeDep, clock: ClockDep
) -> PatientSubmissionOut | JSONResponse:
    """POST /me/responses — patient self-report (with submission_id). 201 «saved» (never a result,
    D-109), or 422 with problems (the response and any critical alert are still stored). Retrying the
    same submission_id returns the same outcome; corrected answers need a new submission_id."""
    assert actor.patient_id is not None
    case = await service.patient_case(session, actor)  # active, else latest (critical answers still alert)
    submission = await service.submit(
        session,
        actor,
        knowledge,
        submission_id=body.submission_id,
        case_id=case.id,
        scale_code=body.scale_code,
        answers=body.answers,
        assignment_id=body.assignment_id,
        taken_at=None,
        now=clock.now(),
    )
    if submission.result is None:
        return _problems(submission)
    return PatientSubmissionOut(response_id=submission.response.id)


@router.get("/me/responses")
async def get_my_responses(
    actor: PatientDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Page[PatientResponseOut]:
    """GET /me/responses — the patient's saved questionnaires: scores and interpretations, without item answers."""
    items, total = await service.patient_responses(session, actor, knowledge, offset=offset, limit=limit)
    return Page(
        items=[
            PatientResponseOut(
                response_id=i.response_id,
                scale_name=i.scale_name,
                taken_on=i.taken_on,
                scale_code=i.scale_code,
                total=i.total,
                band=i.band,
                subscores=i.subscores,
            )
            for i in items
        ],
        total=total,
        offset=offset,
        limit=limit,
    )


@router.post("/cases/{case_id}/ratings", status_code=201, response_model=DoctorResultOut)
async def post_rating(
    case_id: uuid.UUID,
    body: RatingIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> DoctorResultOut | JSONResponse:
    """POST /cases/{case_id}/ratings — doctor's clinician rating."""
    submission = await service.submit(
        session,
        actor,
        knowledge,
        submission_id=body.submission_id,
        case_id=case_id,
        scale_code=body.scale_code,
        answers=body.answers,
        assignment_id=body.assignment_id,
        taken_at=body.taken_at,
        now=clock.now(),
    )
    if submission.result is None:
        return _problems(submission)
    return DoctorResultOut.of(submission.response, submission.result)


@router.get("/patients/{patient_id}/results")
async def get_results(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Page[DoctorResultOut]:
    """GET /patients/{id}/results — doctor; paginated."""
    rows, total = await service.doctor_results(session, actor, patient_id, offset=offset, limit=limit)
    return Page(items=[DoctorResultOut.of(r, res) for r, res in rows], total=total, offset=offset, limit=limit)


@router.get("/me/progress")
async def get_my_progress(actor: PatientDep, session: SessionDep) -> list[SeriesOut]:
    assert actor.patient_id is not None
    series = await service.doctor_progress(session, actor, actor.patient_id)
    return [
        SeriesOut(
            scale_code=scale,
            subscale_code=sub,
            points=[{"taken_on": m.taken_on.isoformat(), "value": m.value} for m in points],
        )
        for (scale, sub), points in sorted(series.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]


@router.get("/patients/{patient_id}/progress")
async def get_progress(patient_id: uuid.UUID, actor: DoctorDep, session: SessionDep) -> list[SeriesOut]:
    """GET /patients/{id}/progress — doctor: values per series."""
    series = await service.doctor_progress(session, actor, patient_id)
    return [
        SeriesOut(
            scale_code=scale,
            subscale_code=sub,
            points=[{"taken_on": m.taken_on.isoformat(), "value": m.value} for m in points],
        )
        for (scale, sub), points in sorted(series.items(), key=lambda kv: (kv[0][0], kv[0][1] or ""))
    ]
