"""Endpoints: adverse events — patient reports, doctor review/record, report-sent mark."""

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from kompas.api.deps import ActorDep, ClockDep, DoctorDep, KnowledgeDep, PatientDep, SessionDep
from kompas.modules.adverse_events import service
from kompas.modules.adverse_events.models import AdverseEvent, AeStatus, DrugRelation
from kompas.modules.adverse_events.simple import router as simple_router
from kompas.modules.cases.domain.profile import Tristate
from kompas.modules.knowledge.bundle import Knowledge

router = APIRouter(tags=["adverse events"])
router.include_router(simple_router)


class ReportIn(BaseModel):
    submission_id: uuid.UUID
    course_id: uuid.UUID | None = None
    symptom_code: str
    symptom_text: str | None = Field(default=None, max_length=1000)
    severity: str
    started_on: date
    ongoing: bool = True
    ended_on: date | None = None  # required when ongoing is false


class RecordIn(ReportIn):
    relation: DrugRelation
    in_label: Tristate | None = None
    report_required: bool = False
    note: str | None = Field(default=None, max_length=2000)


class ReviewIn(BaseModel):
    status: AeStatus
    relation: DrugRelation | None = None
    in_label: Tristate | None = None
    report_required: bool = False
    note: str | None = Field(default=None, max_length=2000)


class ReportSentIn(BaseModel):
    sent_on: date


class EndIn(BaseModel):
    ended_on: date


class PatientEventOut(BaseModel):
    """The patient's view: what they reported and whether the doctor has looked at it."""

    adverse_event_id: uuid.UUID
    symptom: str
    severity: str
    severity_label: str
    drug_name: str | None
    started_on: date | None
    ongoing: bool
    ended_on: date | None
    reviewed: bool

    @classmethod
    def of(cls, e: AdverseEvent, k: Knowledge) -> "PatientEventOut":
        rules = k.adverse_event_rules
        return cls(
            adverse_event_id=e.id,
            symptom=e.symptom_text
            if e.symptom_code == "other" and e.symptom_text
            else rules.symptom_label(e.symptom_code) or e.symptom_code,
            severity=e.severity,
            severity_label="Не оценена" if e.simple_report else rules.severity(e.severity).label,
            drug_name=e.drug_name,
            started_on=e.started_on,
            ongoing=e.ongoing,
            ended_on=e.ended_on,
            reviewed=e.status is not AeStatus.REPORTED,
        )


class DoctorEventOut(PatientEventOut):
    patient_id: uuid.UUID
    simple_report: bool
    onset_text: str | None
    details_complete: bool
    symptom_code: str
    course_id: uuid.UUID | None
    reporter_role: str
    reported_at: datetime
    last_therapy_change_on: date | None
    status: AeStatus
    relation: DrugRelation | None
    in_label: Tristate | None
    report_required: bool
    report_sent_on: date | None
    doctor_note: str | None
    reviewed_at: datetime | None

    @classmethod
    def of(cls, e: AdverseEvent, k: Knowledge) -> "DoctorEventOut":
        return cls(
            **PatientEventOut.of(e, k).model_dump(),
            patient_id=e.patient_id,
            simple_report=e.simple_report,
            onset_text=e.onset_text,
            details_complete=e.details_complete,
            symptom_code=e.symptom_code,
            course_id=e.course_id,
            reporter_role=e.reporter_role.value,
            reported_at=e.created_at,
            last_therapy_change_on=e.last_therapy_change_on,
            status=e.status,
            relation=e.relation,
            in_label=e.in_label,
            report_required=e.report_required,
            report_sent_on=e.report_sent_on,
            doctor_note=e.doctor_note,
            reviewed_at=e.reviewed_at,
        )


@router.get("/adverse-events/options")
async def get_options(knowledge: KnowledgeDep, _actor: ActorDep) -> dict[str, Any]:
    """Symptom list, severities and the official report portal (for the forms)."""
    rules = knowledge.adverse_event_rules
    return {
        "symptoms": [s.model_dump() for s in rules.symptoms],
        "severities": [{"code": s.code, "label": s.label} for s in rules.severities],
        "report_portal": rules.report_portal,
    }


@router.post("/me/adverse-events", status_code=201)
async def post_report(
    body: ReportIn, actor: PatientDep, session: SessionDep, knowledge: KnowledgeDep, clock: ClockDep
) -> PatientEventOut:
    """POST /me/adverse-events — the patient reports a side effect."""
    event = await service.report(session, actor, knowledge, **body.model_dump(), now=clock.now())
    return PatientEventOut.of(event, knowledge)


@router.get("/me/adverse-events")
async def get_my_events(
    actor: PatientDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[PatientEventOut]:
    """GET /me/adverse-events — own reports, without the doctor's assessment."""
    rows = await service.for_patient(session, actor, offset=offset, limit=limit)
    return [PatientEventOut.of(e, knowledge) for e in rows]


@router.get("/adverse-events/todo")
async def get_todo(actor: DoctorDep, session: SessionDep, knowledge: KnowledgeDep) -> list[DoctorEventOut]:
    """GET — the doctor's reminder across all their patients: reports to review and Росздравнадзор
    reports not yet marked as sent."""
    return [DoctorEventOut.of(e, knowledge) for e in await service.doctor_todo(session, actor)]


@router.post("/me/adverse-events/{event_id}/ended")
async def post_ended(
    event_id: uuid.UUID, body: EndIn, actor: PatientDep, session: SessionDep, knowledge: KnowledgeDep, clock: ClockDep
) -> PatientEventOut:
    """POST — the symptom has passed."""
    return PatientEventOut.of(
        await service.end(session, actor, event_id, ended_on=body.ended_on, now=clock.now()), knowledge
    )


@router.get("/patients/{patient_id}/adverse-events")
async def get_events(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[DoctorEventOut]:
    """GET — the doctor's list: to review first, then reports still to send to Росздравнадзор."""
    rows = await service.for_doctor(session, actor, patient_id, offset=offset, limit=limit)
    return [DoctorEventOut.of(e, knowledge) for e in rows]


@router.post("/patients/{patient_id}/adverse-events", status_code=201)
async def post_record(
    patient_id: uuid.UUID,
    body: RecordIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> DoctorEventOut:
    """POST — the doctor records an adverse event directly («Зафиксировать НЯ»)."""
    event = await service.record(session, actor, knowledge, patient_id, **body.model_dump(), now=clock.now())
    return DoctorEventOut.of(event, knowledge)


@router.post("/adverse-events/{event_id}/review")
async def post_review(
    event_id: uuid.UUID, body: ReviewIn, actor: DoctorDep, session: SessionDep, knowledge: KnowledgeDep, clock: ClockDep
) -> DoctorEventOut:
    """POST — confirm (causality required) or dismiss (reason required) a patient's report."""
    event = await service.review(session, actor, event_id, **body.model_dump(), now=clock.now())
    return DoctorEventOut.of(event, knowledge)


@router.post("/adverse-events/{event_id}/report-sent")
async def post_report_sent(
    event_id: uuid.UUID,
    body: ReportSentIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> DoctorEventOut:
    """POST — the doctor sent the Росздравнадзор report (the system never sends it itself)."""
    event = await service.mark_report_sent(session, actor, event_id, sent_on=body.sent_on, now=clock.now())
    return DoctorEventOut.of(event, knowledge)
