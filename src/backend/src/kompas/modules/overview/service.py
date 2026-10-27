"""Patient summaries for the doctor's overview and patient list.

One row per patient of the treating doctor: the case, its main diagnosis, the last ``days`` of the
diary as triage levels (a missing day stays missing, A12), open alerts and the current therapy.
Everything is read in a few batched queries per page, never one query per patient.
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.core.clock import local_date
from kompas.core.errors import PermissionDeniedError
from kompas.modules.audit.models import AuditEntry
from kompas.modules.cases.models import (
    CaseStatus,
    Diagnosis,
    DiagnosisStatus,
    MedicalCase,
    PatientProfileRow,
    Visit,
)
from kompas.modules.diary.models import DiaryEntryRevision, DiaryEntryRow, DiaryTriageRow
from kompas.modules.identity.models import Patient
from kompas.modules.identity.service import ensure_patient_access, list_patients
from kompas.modules.knowledge.triage_rules import TriageLevel
from kompas.modules.monitoring.domain.alerts import AlertStatus
from kompas.modules.monitoring.models import AlertLevel, AlertRow
from kompas.modules.therapy.models import Prescription, PrescriptionItem

ALERT_RANK = {AlertLevel.URGENT: 3, AlertLevel.HIGH: 2, AlertLevel.PLANNED: 1}


@dataclass(frozen=True, slots=True)
class DiaryDay:
    """One calendar day of the diary: ``level`` is None when there is no entry; ``pending`` when the
    entry exists but has no triage yet."""

    day: date
    level: TriageLevel | None
    pending: bool = False


@dataclass(frozen=True, slots=True)
class PatientSummary:
    patient_id: uuid.UUID
    full_name: str
    birth_date: date
    sex: str | None
    has_account: bool
    case_id: uuid.UUID | None
    case_status: CaseStatus | None
    main_icd: str | None
    last_entry_at: datetime | None
    last_visit_at: datetime | None
    last_activity_at: datetime | None
    days: tuple[DiaryDay, ...]
    open_alerts: int
    top_alert_level: AlertLevel | None
    therapy: tuple[str, ...]


async def patient_summaries(
    session: AsyncSession, actor: Actor, *, now: datetime, days: int, offset: int, limit: int
) -> tuple[list[PatientSummary], int]:
    """A page of the doctor's patients with their summaries (same order as the patient list).

    Raises:
        PermissionDeniedError: actor is not a doctor.
    """
    patients, total = await list_patients(session, actor, offset=offset, limit=limit)
    return await _summarize(session, patients, now=now, days=days), total


async def patient_summary(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, *, now: datetime, days: int
) -> PatientSummary:
    """One patient's summary (the card header and pages outside the patient list).

    Raises:
        PermissionDeniedError: actor is not a doctor.
        NotFoundError: missing patient or not this doctor's patient.
    """
    if not actor.is_doctor:
        raise PermissionDeniedError("Доступно только врачу")
    access = await ensure_patient_access(session, actor, patient_id)
    return (await _summarize(session, [access.patient], now=now, days=days))[0]


async def _summarize(
    session: AsyncSession, patients: list[Patient], *, now: datetime, days: int
) -> list[PatientSummary]:
    """Summaries for ``patients`` (in that order) in a few batched queries."""
    ids = [p.id for p in patients]
    if not ids:
        return []

    profiles = {
        row.patient_id: row
        for row in (await session.scalars(select(PatientProfileRow).where(PatientProfileRow.patient_id.in_(ids))))
    }
    # the active case, else the most recent one (closed), per patient
    cases: dict[uuid.UUID, MedicalCase] = {}
    for found in await session.scalars(
        select(MedicalCase)
        .where(MedicalCase.patient_id.in_(ids), MedicalCase.demo.is_(False))
        .order_by((MedicalCase.status == CaseStatus.ACTIVE).desc(), MedicalCase.created_at.desc())
    ):
        cases.setdefault(found.patient_id, found)
    case_ids = [c.id for c in cases.values()]

    last_visit = dict(
        (
            await session.execute(
                select(MedicalCase.patient_id, func.max(Visit.held_at))
                .join(Visit, Visit.case_id == MedicalCase.id)
                .where(MedicalCase.patient_id.in_(ids), MedicalCase.demo.is_(False), Visit.held_at <= now)
                .group_by(MedicalCase.patient_id)
            )
        ).all()
    )
    # Activity means saved patient actions; page views and logins are not tracked in the audit log.
    last_activity = dict(
        (
            await session.execute(
                select(AuditEntry.patient_id, func.max(AuditEntry.at))
                .join(Patient, (Patient.id == AuditEntry.patient_id) & (Patient.user_id == AuditEntry.actor_user_id))
                .where(Patient.id.in_(ids), AuditEntry.at <= now)
                .group_by(AuditEntry.patient_id)
            )
        ).all()
    )

    main_icd = {
        d.case_id: d.icd_code
        for d in await session.scalars(
            select(Diagnosis).where(
                Diagnosis.case_id.in_(case_ids),
                Diagnosis.is_main.is_(True),
                Diagnosis.status != DiagnosisStatus.REMOVED,
            )
        )
    }

    earliest = now.date() - timedelta(days=days + 1)  # widened; trimmed per time zone below
    diary_rows = (
        await session.execute(
            select(DiaryEntryRow.case_id, DiaryEntryRow.day, DiaryTriageRow.level)
            .join(
                DiaryEntryRevision,
                (DiaryEntryRevision.entry_id == DiaryEntryRow.id)
                & (DiaryEntryRevision.revision == DiaryEntryRow.current_revision),
            )
            .outerjoin(DiaryTriageRow, DiaryTriageRow.revision_id == DiaryEntryRevision.id)
            .where(DiaryEntryRow.case_id.in_(case_ids), DiaryEntryRow.day >= earliest)
        )
    ).all()
    levels: dict[uuid.UUID, dict[date, TriageLevel | None]] = defaultdict(dict)
    for case_id, day, level in diary_rows:
        levels[case_id][day] = level
    last_entry = dict(
        (
            await session.execute(
                select(DiaryEntryRow.case_id, func.max(DiaryEntryRevision.created_at))
                .join(DiaryEntryRevision, DiaryEntryRevision.entry_id == DiaryEntryRow.id)
                .where(DiaryEntryRow.case_id.in_(case_ids))
                .group_by(DiaryEntryRow.case_id)
            )
        ).all()
    )

    alerts: dict[uuid.UUID, list[AlertLevel]] = defaultdict(list)
    for patient_id, alert_level in await session.execute(
        select(AlertRow.patient_id, AlertRow.level).where(
            AlertRow.patient_id.in_(ids), AlertRow.status != AlertStatus.HANDLED
        )
    ):
        alerts[patient_id].append(alert_level)

    latest_version = (
        select(Prescription.case_id, func.max(Prescription.version).label("version"))
        .where(Prescription.case_id.in_(case_ids))
        .group_by(Prescription.case_id)
        .subquery()
    )
    therapy: dict[uuid.UUID, list[str]] = defaultdict(list)
    for case_id, trade_name in await session.execute(
        select(Prescription.case_id, PrescriptionItem.trade_name)
        .join(
            latest_version,
            (latest_version.c.case_id == Prescription.case_id) & (latest_version.c.version == Prescription.version),
        )
        .join(PrescriptionItem, PrescriptionItem.prescription_id == Prescription.id)
        .order_by(PrescriptionItem.trade_name)
    ):
        therapy[case_id].append(trade_name)

    summaries = []
    for patient in patients:
        profile = profiles.get(patient.id)
        case = cases.get(patient.id)
        today = local_date(now, ZoneInfo(profile.timezone if profile else "UTC"))
        by_day = levels.get(case.id, {}) if case else {}
        window = tuple(
            DiaryDay(d, by_day.get(d), pending=d in by_day and by_day[d] is None)
            for d in (today - timedelta(days=offset_days) for offset_days in range(days - 1, -1, -1))
        )
        open_levels = alerts.get(patient.id, [])
        summaries.append(
            PatientSummary(
                patient_id=patient.id,
                full_name=patient.full_name,
                birth_date=patient.birth_date,
                sex=profile.sex.value if profile else None,
                has_account=patient.user_id is not None,
                case_id=case.id if case else None,
                case_status=case.status if case else None,
                main_icd=main_icd.get(case.id) if case else None,
                last_entry_at=last_entry.get(case.id) if case else None,
                last_visit_at=last_visit.get(patient.id),
                last_activity_at=last_activity.get(patient.id),
                days=window,
                open_alerts=len(open_levels),
                top_alert_level=max(open_levels, key=ALERT_RANK.__getitem__) if open_levels else None,
                therapy=tuple(therapy.get(case.id, [])) if case else (),
            )
        )
    return summaries
