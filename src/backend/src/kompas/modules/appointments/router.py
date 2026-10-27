"""Endpoints: planned visits — doctor plans/moves/cancels/completes, patient views and asks for changes."""

import uuid
from datetime import datetime, timedelta
from typing import Literal

from fastapi import APIRouter, Query
from pydantic import AwareDatetime, BaseModel, Field

from kompas.api.deps import ClockDep, DoctorDep, KnowledgeDep, PatientDep, SessionDep
from kompas.modules.appointments import service
from kompas.modules.appointments.models import (
    AppointmentChangeRequest,
    AppointmentMode,
    AppointmentStatus,
    RequestKind,
    RequestStatus,
)
from kompas.modules.appointments.service import AppointmentView
from kompas.modules.cases.models import VisitKind

router = APIRouter(tags=["appointments"])

Scope = Literal["upcoming", "past", "all"]


class PlanIn(BaseModel):
    submission_id: uuid.UUID
    starts_at: AwareDatetime
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    kind: VisitKind = VisitKind.REPEAT
    mode: AppointmentMode = AppointmentMode.IN_PERSON
    place: str | None = Field(default=None, max_length=300)
    link: str | None = Field(default=None, max_length=1000)
    purpose: str | None = Field(default=None, max_length=1000)
    note_for_patient: str | None = Field(default=None, max_length=1000)


class UpdateIn(BaseModel):
    """Only the fields present are changed (``null`` clears place/link/purpose/note)."""

    starts_at: AwareDatetime | None = None
    duration_minutes: int | None = Field(default=None, ge=5, le=480)
    mode: AppointmentMode | None = None
    place: str | None = Field(default=None, max_length=300)
    link: str | None = Field(default=None, max_length=1000)
    purpose: str | None = Field(default=None, max_length=1000)
    note_for_patient: str | None = Field(default=None, max_length=1000)


class CancelIn(BaseModel):
    reason: str | None = Field(default=None, max_length=1000)


class CompleteIn(BaseModel):
    visit_id: uuid.UUID | None = None
    patient_summary: str | None = Field(default=None, max_length=5000)


class SummaryIn(BaseModel):
    patient_summary: str | None = Field(default=None, max_length=5000)


class ChangeRequestIn(BaseModel):
    submission_id: uuid.UUID
    kind: RequestKind
    proposed_starts_at: AwareDatetime | None = None
    reason: str | None = Field(default=None, max_length=1000)


class DecisionIn(BaseModel):
    accept: bool
    starts_at: AwareDatetime | None = None  # reschedule: the final time (default: the patient's proposal)
    reply: str | None = Field(default=None, max_length=1000)


class RequestOut(BaseModel):
    request_id: uuid.UUID
    kind: RequestKind
    proposed_starts_at: datetime | None
    reason: str | None
    status: RequestStatus
    reply: str | None
    created_at: datetime
    decided_at: datetime | None

    @classmethod
    def of(cls, r: AppointmentChangeRequest) -> "RequestOut":
        return cls(
            request_id=r.id,
            kind=r.kind,
            proposed_starts_at=r.proposed_starts_at,
            reason=r.reason,
            status=r.status,
            reply=r.reply,
            created_at=r.created_at,
            decided_at=r.decided_at,
        )


class PatientAppointmentOut(BaseModel):
    """The patient's view: when, where, with whom; the doctor's purpose and notes are not included."""

    appointment_id: uuid.UUID
    starts_at: datetime
    duration_minutes: int
    kind: VisitKind
    mode: AppointmentMode
    place: str | None
    link: str | None
    doctor_name: str
    status: AppointmentStatus
    note_for_patient: str | None
    patient_summary: str | None
    request: RequestOut | None

    @classmethod
    def of(cls, v: AppointmentView) -> "PatientAppointmentOut":
        a = v.appointment
        return cls(
            appointment_id=a.id,
            starts_at=a.starts_at,
            duration_minutes=a.duration_minutes,
            kind=a.kind,
            mode=a.mode,
            place=a.place,
            link=a.link,
            doctor_name=v.doctor_name,
            status=a.status,
            note_for_patient=a.note_for_patient,
            patient_summary=a.patient_summary,
            request=RequestOut.of(v.request) if v.request else None,
        )


class DoctorAppointmentOut(PatientAppointmentOut):
    patient_id: uuid.UUID
    case_id: uuid.UUID
    purpose: str | None
    cancel_reason: str | None
    visit_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime

    @classmethod
    def of(cls, v: AppointmentView) -> "DoctorAppointmentOut":
        a = v.appointment
        return cls(
            **PatientAppointmentOut.of(v).model_dump(),
            patient_id=a.patient_id,
            case_id=a.case_id,
            purpose=a.purpose,
            cancel_reason=a.cancel_reason,
            visit_id=a.visit_id,
            created_at=a.created_at,
            updated_at=a.updated_at,
        )


class ScheduleItemOut(DoctorAppointmentOut):
    patient_name: str


class PendingRequestOut(RequestOut):
    appointment_id: uuid.UUID
    patient_id: uuid.UUID
    starts_at: datetime


@router.post("/patients/{patient_id}/appointments", status_code=201)
async def post_plan(
    patient_id: uuid.UUID,
    body: PlanIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> DoctorAppointmentOut:
    """POST — the treating doctor plans a visit; the patient is notified."""
    appointment = await service.plan(session, actor, knowledge, patient_id, **body.model_dump(), now=clock.now())
    return DoctorAppointmentOut.of((await service.views(session, [appointment]))[0])


@router.get("/patients/{patient_id}/appointments")
async def get_patient_appointments(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    clock: ClockDep,
    scope: Scope = "all",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[DoctorAppointmentOut]:
    """GET — the patient's visits for the doctor (``upcoming`` nearest first; ``past``/``all`` newest first)."""
    views = await service.for_doctor(
        session, actor, patient_id, scope=scope, now=clock.now(), offset=offset, limit=limit
    )
    return [DoctorAppointmentOut.of(v) for v in views]


@router.get("/appointments/schedule")
async def get_schedule(
    actor: DoctorDep,
    session: SessionDep,
    clock: ClockDep,
    start: AwareDatetime | None = None,
    end: AwareDatetime | None = None,
) -> list[ScheduleItemOut]:
    """GET — the doctor's planned visits across patients (default: from now for 7 days)."""
    begin = start or clock.now()
    rows = await service.schedule(session, actor, start=begin, end=end or begin + timedelta(days=7))
    return [ScheduleItemOut(**DoctorAppointmentOut.of(v).model_dump(), patient_name=name) for v, name in rows]


@router.get("/appointments/requests")
async def get_requests(actor: DoctorDep, session: SessionDep) -> list[PendingRequestOut]:
    """GET — patient requests to reschedule/cancel waiting for the doctor's decision."""
    return [
        PendingRequestOut(
            **RequestOut.of(r).model_dump(), appointment_id=a.id, patient_id=a.patient_id, starts_at=a.starts_at
        )
        for r, a in await service.pending_requests(session, actor)
    ]


@router.patch("/appointments/{appointment_id}")
async def patch_appointment(
    appointment_id: uuid.UUID,
    body: UpdateIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> DoctorAppointmentOut:
    """PATCH — the doctor moves or edits a planned visit; the patient is notified of visible changes."""
    appointment = await service.update(
        session, actor, knowledge, appointment_id, body.model_dump(exclude_unset=True), now=clock.now()
    )
    return DoctorAppointmentOut.of((await service.views(session, [appointment]))[0])


@router.post("/appointments/{appointment_id}/cancel")
async def post_cancel(
    appointment_id: uuid.UUID, body: CancelIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> DoctorAppointmentOut:
    """POST — the doctor cancels a planned visit."""
    appointment = await service.cancel(session, actor, appointment_id, reason=body.reason, now=clock.now())
    return DoctorAppointmentOut.of((await service.views(session, [appointment]))[0])


@router.post("/appointments/{appointment_id}/complete")
async def post_complete(
    appointment_id: uuid.UUID, body: CompleteIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> DoctorAppointmentOut:
    """POST — the visit was held: link the visit record, optionally write a summary for the patient."""
    appointment = await service.complete(session, actor, appointment_id, **body.model_dump(), now=clock.now())
    return DoctorAppointmentOut.of((await service.views(session, [appointment]))[0])


@router.post("/appointments/{appointment_id}/summary")
async def post_summary(
    appointment_id: uuid.UUID, body: SummaryIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> DoctorAppointmentOut:
    """POST — write or correct the patient-facing summary of a held visit."""
    appointment = await service.set_summary(
        session, actor, appointment_id, patient_summary=body.patient_summary, now=clock.now()
    )
    return DoctorAppointmentOut.of((await service.views(session, [appointment]))[0])


@router.post("/appointment-requests/{request_id}/decision")
async def post_decision(
    request_id: uuid.UUID,
    body: DecisionIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> DoctorAppointmentOut:
    """POST — accept (reschedule: moves the visit; cancel: cancels it) or decline a patient request."""
    _, appointment = await service.decide(session, actor, knowledge, request_id, **body.model_dump(), now=clock.now())
    return DoctorAppointmentOut.of((await service.views(session, [appointment]))[0])


@router.get("/me/appointments")
async def get_my_appointments(
    actor: PatientDep,
    session: SessionDep,
    clock: ClockDep,
    scope: Scope = "all",
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[PatientAppointmentOut]:
    """GET /me/appointments — own visits: ``upcoming`` (nearest first) or ``past``/``all`` (newest first)."""
    views = await service.for_patient(session, actor, scope=scope, now=clock.now(), offset=offset, limit=limit)
    return [PatientAppointmentOut.of(v) for v in views]


@router.post("/me/appointments/{appointment_id}/change-requests", status_code=201)
async def post_change_request(
    appointment_id: uuid.UUID,
    body: ChangeRequestIn,
    actor: PatientDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> RequestOut:
    """POST — ask the doctor to reschedule or cancel; the visit stays as it is until the doctor decides."""
    request = await service.request_change(
        session, actor, knowledge, appointment_id, **body.model_dump(), now=clock.now()
    )
    return RequestOut.of(request)
