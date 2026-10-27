"""Endpoints: GET /overview/patients (and /{id}) — the doctor's patients with diary days, alerts and therapy."""

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel

from kompas.api.deps import ClockDep, DoctorDep, SessionDep
from kompas.modules.identity.schemas import Page
from kompas.modules.overview import service

router = APIRouter(tags=["overview"])


class DiaryDayOut(BaseModel):
    day: date
    level: str | None
    pending: bool


class PatientSummaryOut(BaseModel):
    patient_id: uuid.UUID
    full_name: str
    birth_date: date
    sex: str | None
    has_account: bool
    case_id: uuid.UUID | None
    case_status: str | None
    main_icd: str | None
    last_entry_at: datetime | None
    last_visit_at: datetime | None
    last_activity_at: datetime | None
    days: list[DiaryDayOut]
    open_alerts: int
    top_alert_level: str | None
    therapy: list[str]

    @classmethod
    def of(cls, s: service.PatientSummary) -> "PatientSummaryOut":
        return cls(
            patient_id=s.patient_id,
            full_name=s.full_name,
            birth_date=s.birth_date,
            sex=s.sex,
            has_account=s.has_account,
            case_id=s.case_id,
            case_status=s.case_status.value if s.case_status else None,
            main_icd=s.main_icd,
            last_entry_at=s.last_entry_at,
            last_visit_at=s.last_visit_at,
            last_activity_at=s.last_activity_at,
            days=[DiaryDayOut(day=d.day, level=d.level.value if d.level else None, pending=d.pending) for d in s.days],
            open_alerts=s.open_alerts,
            top_alert_level=s.top_alert_level.value if s.top_alert_level else None,
            therapy=list(s.therapy),
        )


@router.get("/overview/patients")
async def get_patient_summaries(
    actor: DoctorDep,
    session: SessionDep,
    clock: ClockDep,
    days: int = Query(14, ge=1, le=60),
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Page[PatientSummaryOut]:
    """GET /overview/patients?days&offset&limit — doctor; same order as GET /patients."""
    rows, total = await service.patient_summaries(
        session, actor, now=clock.now(), days=days, offset=offset, limit=limit
    )
    return Page(items=[PatientSummaryOut.of(r) for r in rows], total=total, offset=offset, limit=limit)


@router.get("/overview/patients/{patient_id}")
async def get_patient_summary(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    clock: ClockDep,
    days: int = Query(14, ge=1, le=60),
) -> PatientSummaryOut:
    """GET /overview/patients/{id}?days — doctor; one patient's summary (404 for foreign patients)."""
    return PatientSummaryOut.of(await service.patient_summary(session, actor, patient_id, now=clock.now(), days=days))
