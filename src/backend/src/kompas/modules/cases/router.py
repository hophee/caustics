"""Endpoints under /patients/{patient_id}: profile, cases, visits, diagnoses."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Body
from pydantic import BaseModel, Field
from sqlalchemy import select

from kompas.api.deps import ActorDep, ClockDep, DoctorDep, PatientDep, SessionDep
from kompas.core.jsonable import to_jsonable
from kompas.json_input import parse_patient
from kompas.modules.cases import service
from kompas.modules.cases.models import (
    CaseStatus,
    Diagnosis,
    DiagnosisStatus,
    MedicalCase,
    PatientProfileRow,
    Visit,
    VisitKind,
)

router = APIRouter(tags=["cases"])


class CaseOut(BaseModel):
    case_id: uuid.UUID
    patient_id: uuid.UUID
    status: CaseStatus
    opened_at: datetime
    closed_at: datetime | None

    @classmethod
    def of(cls, case: MedicalCase) -> "CaseOut":
        return cls(
            case_id=case.id,
            patient_id=case.patient_id,
            status=case.status,
            opened_at=case.created_at,
            closed_at=case.closed_at,
        )


class VisitIn(BaseModel):
    kind: VisitKind
    held_at: datetime
    content: dict[str, Any] = Field(default_factory=dict)


class VisitOut(BaseModel):
    visit_id: uuid.UUID
    kind: VisitKind
    held_at: datetime


class DiagnosisIn(BaseModel):
    icd_code: str
    is_main: bool = True
    provisional: bool = False


class DiagnosisOut(BaseModel):
    diagnosis_id: uuid.UUID
    icd_code: str
    is_main: bool
    status: DiagnosisStatus

    @classmethod
    def of(cls, d: Diagnosis) -> "DiagnosisOut":
        return cls(diagnosis_id=d.id, icd_code=d.icd_code, is_main=d.is_main, status=d.status)


async def _profile_out(session: SessionDep, actor: ActorDep, patient_id: uuid.UUID) -> dict[str, Any]:
    profile = await service.get_profile(session, actor, patient_id)
    row = await session.get(PatientProfileRow, patient_id)
    data: dict[str, Any] = to_jsonable(profile)
    data["diagnoses"] = [d.code for d in profile.diagnoses]
    data["current_medications_reviewed_at"] = (
        row.current_medications_reviewed_at.isoformat() if row and row.current_medications_reviewed_at else None
    )
    return data


@router.get("/patients/{patient_id}/profile")
async def get_profile(patient_id: uuid.UUID, actor: DoctorDep, session: SessionDep) -> dict[str, Any]:
    """GET /patients/{id}/profile — doctor (patient sees a reduced view later)."""
    return await _profile_out(session, actor, patient_id)


@router.put("/patients/{patient_id}/profile")
async def put_profile(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    clock: ClockDep,
    body: dict[str, Any] = Body(...),
) -> dict[str, Any]:
    """PUT /patients/{id}/profile — doctor; body uses explicit null vs [] for list facts."""
    await service.update_profile(session, actor, patient_id, parse_patient(body, "profile"), now=clock.now())
    return await _profile_out(session, actor, patient_id)


@router.post("/patients/{patient_id}/profile/medications-reviewed", status_code=204)
async def post_medications_reviewed(
    patient_id: uuid.UUID, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> None:
    """POST /patients/{id}/profile/medications-reviewed — the current-medication list is complete."""
    await service.mark_medications_reviewed(session, actor, patient_id, now=clock.now())


@router.post("/patients/{patient_id}/cases", status_code=201)
async def post_case(patient_id: uuid.UUID, actor: DoctorDep, session: SessionDep, clock: ClockDep) -> CaseOut:
    """POST /patients/{id}/cases — open the active case (409 if one exists)."""
    return CaseOut.of(await service.open_case(session, actor, patient_id, now=clock.now()))


@router.get("/patients/{patient_id}/cases/active")
async def get_active_case(patient_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> CaseOut:
    """GET /patients/{id}/cases/active — the active case (doctor or the patient themself)."""
    return CaseOut.of(await service.active_case(session, actor, patient_id))


@router.post("/cases/{case_id}/close")
async def post_case_close(case_id: uuid.UUID, actor: DoctorDep, session: SessionDep, clock: ClockDep) -> CaseOut:
    """POST /cases/{case_id}/close."""
    return CaseOut.of(await service.close_case(session, actor, case_id, now=clock.now()))


@router.post("/cases/{case_id}/visits", status_code=201)
async def post_visit(
    case_id: uuid.UUID, body: VisitIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> VisitOut:
    """POST /cases/{case_id}/visits."""
    visit = await service.add_visit(
        session, actor, case_id, kind=body.kind, held_at=body.held_at, content=body.content, now=clock.now()
    )
    return VisitOut(visit_id=visit.id, kind=visit.kind, held_at=visit.held_at)


class VisitDetailOut(VisitOut):
    content: dict[str, Any]


@router.get("/me/visits")
async def get_my_visits(actor: PatientDep, session: SessionDep) -> list[VisitDetailOut]:
    rows = await session.scalars(
        select(Visit)
        .join(MedicalCase, MedicalCase.id == Visit.case_id)
        .where(MedicalCase.patient_id == actor.patient_id)
        .order_by(Visit.held_at.desc())
    )
    return [VisitDetailOut(visit_id=v.id, kind=v.kind, held_at=v.held_at, content=dict(v.content)) for v in rows]


@router.get("/cases/{case_id}/visits")
async def get_visits(case_id: uuid.UUID, actor: DoctorDep, session: SessionDep) -> list[VisitDetailOut]:
    """GET /cases/{case_id}/visits — newest first, with the visit record."""
    visits = await service.list_visits(session, actor, case_id)
    return [VisitDetailOut(visit_id=v.id, kind=v.kind, held_at=v.held_at, content=dict(v.content)) for v in visits]


@router.get("/cases/{case_id}/diagnoses")
async def get_diagnoses(case_id: uuid.UUID, actor: DoctorDep, session: SessionDep) -> list[DiagnosisOut]:
    """GET /cases/{case_id}/diagnoses — not removed, main first."""
    return [DiagnosisOut.of(d) for d in await service.list_diagnoses(session, actor, case_id)]


@router.post("/cases/{case_id}/diagnoses", status_code=201)
async def post_diagnosis(
    case_id: uuid.UUID, body: DiagnosisIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> DiagnosisOut:
    """POST /cases/{case_id}/diagnoses."""
    diagnosis = await service.set_diagnosis(
        session,
        actor,
        case_id,
        icd_code=body.icd_code,
        is_main=body.is_main,
        provisional=body.provisional,
        now=clock.now(),
    )
    return DiagnosisOut.of(diagnosis)


@router.put("/cases/{case_id}/visits/{visit_id}")
async def put_visit(
    case_id: uuid.UUID, visit_id: uuid.UUID, body: VisitIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> VisitDetailOut:
    visit = await service.update_visit(
        session, actor, case_id, visit_id, kind=body.kind, held_at=body.held_at, content=body.content, now=clock.now()
    )
    return VisitDetailOut(visit_id=visit.id, kind=visit.kind, held_at=visit.held_at, content=visit.content)
