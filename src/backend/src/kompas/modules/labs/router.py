"""Endpoints: demo lab values (A13) — doctor only; every payload is flagged ``demo: true``."""

import uuid
from datetime import date, datetime
from typing import Any, Literal

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.api.deps import ClockDep, DoctorDep, KnowledgeDep, SessionDep
from kompas.core.clock import local_date
from kompas.modules.cases.service import patient_timezone
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.labs import service
from kompas.modules.labs.models import DemoLabValue

router = APIRouter(tags=["demo labs"])

DEMO_LABEL = "Демонстрационные данные"


class LabValueIn(BaseModel):
    submission_id: uuid.UUID
    code: str
    value: float
    measured_on: date


class LabValueOut(BaseModel):
    lab_id: uuid.UUID
    code: str
    label: str
    unit: str
    value: float
    measured_on: date
    fresh: bool  # within rules/labs.yaml stale_after_days; a stale value is not used by the checks
    entered_at: datetime
    demo: Literal[True] = True

    @classmethod
    def of(cls, row: DemoLabValue, k: Knowledge, today: date) -> "LabValueOut":
        test = k.lab_rules.test(row.code)
        return cls(
            lab_id=row.id,
            code=row.code,
            label=test.label if test else row.code,
            unit=test.unit if test else "",
            value=row.value,
            measured_on=row.measured_on,
            fresh=(today - row.measured_on).days <= k.lab_rules.stale_after_days,
            entered_at=row.created_at,
        )


class DemoLabsOut(BaseModel):
    demo: Literal[True] = True
    demo_label: str = DEMO_LABEL
    values: list[LabValueOut]  # newest measurement first


async def _today(session: AsyncSession, patient_id: uuid.UUID, now: datetime) -> date:
    return local_date(now, await patient_timezone(session, patient_id))


@router.get("/demo-labs/options")
async def get_options(knowledge: KnowledgeDep, _actor: DoctorDep) -> dict[str, Any]:
    """The demo tests (code, label, unit, accepted input range) for the entry form."""
    return {
        "demo": True,
        "demo_label": DEMO_LABEL,
        "tests": [t.model_dump() for t in knowledge.lab_rules.tests],
        "stale_after_days": knowledge.lab_rules.stale_after_days,
    }


@router.get("/patients/{patient_id}/demo-labs")
async def get_labs(
    patient_id: uuid.UUID, actor: DoctorDep, session: SessionDep, knowledge: KnowledgeDep, clock: ClockDep
) -> DemoLabsOut:
    """GET — the patient's demo lab values (profile box «Анализы · демо»)."""
    rows = await service.for_doctor(session, actor, patient_id)
    today = await _today(session, patient_id, clock.now())
    return DemoLabsOut(values=[LabValueOut.of(r, knowledge, today) for r in rows])


@router.post("/patients/{patient_id}/demo-labs", status_code=201)
async def post_lab(
    patient_id: uuid.UUID,
    body: LabValueIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> LabValueOut:
    """POST — enter a demo lab value; the basket checks use it from now on."""
    row = await service.add(session, actor, knowledge, patient_id, **body.model_dump(), now=clock.now())
    return LabValueOut.of(row, knowledge, await _today(session, patient_id, clock.now()))


@router.delete("/patients/{patient_id}/demo-labs/{lab_id}")
async def delete_lab(
    patient_id: uuid.UUID,
    lab_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> LabValueOut:
    """DELETE — remove a demo value (kept for the audit trail, no longer used by the checks)."""
    row = await service.remove(session, actor, patient_id, lab_id, now=clock.now())
    return LabValueOut.of(row, knowledge, await _today(session, patient_id, clock.now()))
