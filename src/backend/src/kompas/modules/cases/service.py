"""Case use cases: safety profile, case lifecycle, visits, diagnoses. Every function checks access
first; clinical writes are treating-doctor only, require an active case, and are audited."""

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.core.errors import InvalidStateError, NotFoundError, PermissionDeniedError
from kompas.core.icd import IcdCode
from kompas.modules.audit import service as audit
from kompas.modules.cases.domain.encounter import validate_encounter
from kompas.modules.cases.domain.profile import PatientProfile
from kompas.modules.cases.models import (
    CaseStatus,
    Diagnosis,
    DiagnosisStatus,
    MedicalCase,
    PatientProfileRow,
    Visit,
    VisitKind,
)
from kompas.modules.identity.models import Patient
from kompas.modules.identity.service import PatientAccess, ensure_patient_access
from kompas.modules.therapy.models import CurrentTherapyReview


async def require_treating_doctor(session: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> PatientAccess:
    """Access check for clinical writes: NotFound for foreign/missing, PermissionDenied for the patient."""
    access = await ensure_patient_access(session, actor, patient_id)
    if not access.is_treating_doctor:
        raise PermissionDeniedError("Действие доступно только лечащему врачу")
    return access


async def patient_timezone(session: AsyncSession, patient_id: uuid.UUID) -> ZoneInfo:
    """The patient's time zone from the safety profile (local dates everywhere)."""
    tz = await session.scalar(select(PatientProfileRow.timezone).where(PatientProfileRow.patient_id == patient_id))
    return ZoneInfo(tz or "Europe/Moscow")


def _tuple(values: list[str] | None) -> tuple[str, ...] | None:
    return None if values is None else tuple(values)


def _list(values: tuple[str, ...] | None) -> list[str] | None:
    return None if values is None else list(values)


async def _active_case_of(session: AsyncSession, patient_id: uuid.UUID) -> MedicalCase | None:
    return await session.scalar(
        select(MedicalCase).where(
            MedicalCase.patient_id == patient_id, MedicalCase.status == CaseStatus.ACTIVE, MedicalCase.demo.is_(False)
        )
    )


async def profile_of(session: AsyncSession, access: PatientAccess) -> PatientProfile:
    """Domain profile for a patient whose access was already checked (used by other modules)."""
    row = await session.get(PatientProfileRow, access.patient.id)
    if row is None:
        raise NotFoundError("Профиль пациента не найден")
    main: list[IcdCode] = []
    case = await _active_case_of(session, access.patient.id)
    if case is not None:
        rows = await session.scalars(
            select(Diagnosis).where(
                Diagnosis.case_id == case.id, Diagnosis.is_main.is_(True), Diagnosis.status != DiagnosisStatus.REMOVED
            )
        )
        main = [IcdCode(d.icd_code, d.icd_original) for d in rows]
    comorbid = [IcdCode.parse(code) for code in row.diagnoses if code not in {m.code for m in main}]
    return PatientProfile(
        birth_date=access.patient.birth_date,
        sex=row.sex,
        timezone=ZoneInfo(row.timezone),
        pregnant=row.pregnant,
        lactating=row.lactating,
        substance_use=row.substance_use,
        suicide_risk=row.suicide_risk,
        diagnoses=(*main, *comorbid),
        allergies=_tuple(row.allergies),
        intolerant_excipients=_tuple(row.intolerant_excipients),
        past_intolerance=_tuple(row.past_intolerance),
        past_inefficacy=_tuple(row.past_inefficacy),
        weight_kg=row.weight_kg,
    )


async def case_for_actor(
    session: AsyncSession, actor: Actor, case_id: uuid.UUID, *, for_update: bool = False, active: bool = False
) -> MedicalCase:
    """Resolve a case id to the case after ``ensure_patient_access`` on its patient.
    ``for_update`` locks the row (serialises confirm vs close); ``active`` requires an active case.

    Raises:
        NotFoundError: missing or foreign case. InvalidStateError: ``active`` and the case is closed.
    """
    case = await session.get(MedicalCase, case_id)
    if case is None:
        raise NotFoundError("Случай не найден")
    try:
        await ensure_patient_access(session, actor, case.patient_id)
    except NotFoundError:
        raise NotFoundError("Случай не найден") from None
    if for_update:  # lock only after the access check (no locking of other doctors' rows)
        locked = await session.get(MedicalCase, case_id, with_for_update=True, populate_existing=True)
        assert locked is not None
        case = locked
    if active and case.status is not CaseStatus.ACTIVE:
        raise InvalidStateError("Случай закрыт")
    return case


async def get_profile(session: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> PatientProfile:
    """Domain profile: birth date (identity) + profile row + main diagnoses of the active case.

    Raises:
        NotFoundError: no access.
    """
    return await profile_of(session, await ensure_patient_access(session, actor, patient_id))


async def update_profile(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, profile: PatientProfile, *, now: datetime
) -> PatientProfile:
    """Treating doctor replaces the safety profile (all list facts explicit, D-063); audited.

    Raises:
        NotFoundError: no access. PermissionDeniedError: the actor is the patient (role mismatch).
    """
    access = await require_treating_doctor(session, actor, patient_id)
    row = await session.get(PatientProfileRow, patient_id, with_for_update=True)
    if row is None:
        raise NotFoundError("Профиль пациента не найден")
    case = await _active_case_of(session, patient_id)
    main_codes: set[str] = set()
    if case is not None:
        main = await main_diagnosis(session, case.id)
        main_codes = {main.icd_code} if main else set()
    access.patient.birth_date = profile.birth_date
    row.sex = profile.sex
    row.timezone = profile.timezone.key
    row.pregnant = profile.pregnant
    row.lactating = profile.lactating
    row.substance_use = profile.substance_use
    row.suicide_risk = profile.suicide_risk
    row.diagnoses = [d.code for d in profile.diagnoses if d.code not in main_codes]
    row.allergies = _list(profile.allergies)
    row.intolerant_excipients = _list(profile.intolerant_excipients)
    row.past_intolerance = _list(profile.past_intolerance)
    row.past_inefficacy = _list(profile.past_inefficacy)
    row.weight_kg = profile.weight_kg
    row.updated_at = now
    row.updated_by = actor.user_id
    await audit.record(
        session,
        actor,
        "profile.updated",
        entity_type="patient_profile",
        entity_id=patient_id,
        patient_id=patient_id,
        details={},
        now=now,
    )
    await session.flush()
    return await profile_of(session, access)


async def mark_medications_reviewed(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, *, now: datetime
) -> None:
    """Treating doctor confirms the current-medication list is complete (sets the review time); audited."""
    await require_treating_doctor(session, actor, patient_id)
    row = await session.get(PatientProfileRow, patient_id, with_for_update=True)
    if row is None:
        raise NotFoundError("Профиль пациента не найден")
    row.current_medications_reviewed_at = now
    await audit.record(
        session,
        actor,
        "profile.medications_reviewed",
        entity_type="patient_profile",
        entity_id=patient_id,
        patient_id=patient_id,
        details={},
        now=now,
    )


async def active_case(session: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> MedicalCase:
    """The patient's active (non-demo) case.

    Raises:
        NotFoundError: none or no access.
    """
    await ensure_patient_access(session, actor, patient_id)
    case = await _active_case_of(session, patient_id)
    if case is None:
        raise NotFoundError("У пациента нет активного случая")
    return case


async def open_case(session: AsyncSession, actor: Actor, patient_id: uuid.UUID, *, now: datetime) -> MedicalCase:
    """Treating doctor opens a case; audited.

    Raises:
        InvalidStateError: an active case already exists (A02).
    """
    await session.get(Patient, patient_id, with_for_update=True, populate_existing=True)
    await require_treating_doctor(session, actor, patient_id)
    if await _active_case_of(session, patient_id) is not None:
        raise InvalidStateError("У пациента уже есть активный случай")
    case = MedicalCase(
        id=uuid.uuid4(),
        created_at=now,
        patient_id=patient_id,
        status=CaseStatus.ACTIVE,
        closed_at=None,
        closed_by=None,
        demo=False,
    )
    session.add(case)
    await session.flush()
    await session.execute(
        update(CurrentTherapyReview)
        .where(CurrentTherapyReview.patient_id == patient_id, CurrentTherapyReview.case_id.is_(None))
        .values(case_id=case.id)
    )
    await audit.record(
        session,
        actor,
        "case.opened",
        entity_type="medical_case",
        entity_id=case.id,
        patient_id=patient_id,
        details={},
        now=now,
    )
    return case


async def close_case(session: AsyncSession, actor: Actor, case_id: uuid.UUID, *, now: datetime) -> MedicalCase:
    """Treating doctor closes a case (row locked); prescriptions are kept; audited.

    Raises:
        InvalidStateError: already closed.
    """
    case = await case_for_actor(session, actor, case_id, for_update=True)
    await require_treating_doctor(session, actor, case.patient_id)
    if case.status is CaseStatus.CLOSED:
        raise InvalidStateError("Случай уже закрыт")
    case.status = CaseStatus.CLOSED
    case.closed_at = now
    case.closed_by = actor.user_id
    await audit.record(
        session,
        actor,
        "case.closed",
        entity_type="medical_case",
        entity_id=case.id,
        patient_id=case.patient_id,
        details={},
        now=now,
    )
    await session.flush()
    return case


async def add_visit(
    session: AsyncSession,
    actor: Actor,
    case_id: uuid.UUID,
    *,
    kind: VisitKind,
    held_at: datetime,
    content: dict[str, object],
    now: datetime,
) -> Visit:
    """Treating doctor records a visit in an active case; audited."""
    case = await case_for_actor(session, actor, case_id, active=True)
    await require_treating_doctor(session, actor, case.patient_id)
    validate_encounter(content)
    await _validate_encounter_references(session, case.id, content)
    assert actor.doctor_id is not None
    visit = Visit(
        id=uuid.uuid4(),
        created_at=now,
        case_id=case.id,
        kind=kind,
        held_at=held_at,
        doctor_id=actor.doctor_id,
        content=content,
    )
    session.add(visit)
    await session.flush()
    await audit.record(
        session,
        actor,
        "visit.added",
        entity_type="visit",
        entity_id=visit.id,
        patient_id=case.patient_id,
        details={"kind": kind.value},
        now=now,
    )
    return visit


async def set_diagnosis(
    session: AsyncSession,
    actor: Actor,
    case_id: uuid.UUID,
    *,
    icd_code: str,
    is_main: bool,
    provisional: bool,
    now: datetime,
) -> Diagnosis:
    """Add a diagnosis to an active case (``IcdCode.parse``); a new main one demotes the previous;
    audited."""
    case = await case_for_actor(session, actor, case_id, active=True, for_update=True)
    await require_treating_doctor(session, actor, case.patient_id)
    code = IcdCode.parse(icd_code)
    if is_main:
        await session.execute(
            update(Diagnosis)
            .where(
                Diagnosis.case_id == case.id, Diagnosis.is_main.is_(True), Diagnosis.status != DiagnosisStatus.REMOVED
            )
            .values(is_main=False)
        )
    diagnosis = Diagnosis(
        id=uuid.uuid4(),
        created_at=now,
        case_id=case.id,
        icd_code=code.code,
        icd_original=code.original,
        is_main=is_main,
        status=DiagnosisStatus.PROVISIONAL if provisional else DiagnosisStatus.ESTABLISHED,
    )
    session.add(diagnosis)
    await session.flush()
    await audit.record(
        session,
        actor,
        "diagnosis.set",
        entity_type="diagnosis",
        entity_id=diagnosis.id,
        patient_id=case.patient_id,
        details={"icd": code.code, "main": is_main, "provisional": provisional},
        now=now,
    )
    return diagnosis


async def main_diagnosis(session: AsyncSession, case_id: uuid.UUID) -> Diagnosis | None:
    """Main established (else provisional) diagnosis — used by selection and provenance."""
    rows = await session.scalars(
        select(Diagnosis)
        .where(Diagnosis.case_id == case_id, Diagnosis.is_main.is_(True), Diagnosis.status != DiagnosisStatus.REMOVED)
        .order_by(Diagnosis.created_at.desc())
    )
    found = list(rows.all())
    established = [d for d in found if d.status is DiagnosisStatus.ESTABLISHED]
    return (established or found)[0] if found else None


async def list_visits(session: AsyncSession, actor: Actor, case_id: uuid.UUID) -> list[Visit]:
    """Visits of a case, newest first (treating doctor)."""
    case = await case_for_actor(session, actor, case_id)
    await require_treating_doctor(session, actor, case.patient_id)
    rows = await session.scalars(select(Visit).where(Visit.case_id == case.id).order_by(Visit.held_at.desc()))
    return list(rows.all())


async def list_diagnoses(session: AsyncSession, actor: Actor, case_id: uuid.UUID) -> list[Diagnosis]:
    """Diagnoses of a case that are not removed, main first (treating doctor)."""
    case = await case_for_actor(session, actor, case_id)
    await require_treating_doctor(session, actor, case.patient_id)
    rows = await session.scalars(
        select(Diagnosis)
        .where(Diagnosis.case_id == case.id, Diagnosis.status != DiagnosisStatus.REMOVED)
        .order_by(Diagnosis.is_main.desc(), Diagnosis.created_at)
    )
    return list(rows.all())


async def update_visit(
    session: AsyncSession,
    actor: Actor,
    case_id: uuid.UUID,
    visit_id: uuid.UUID,
    *,
    kind: VisitKind,
    held_at: datetime,
    content: dict[str, object],
    now: datetime,
) -> Visit:
    case = await case_for_actor(session, actor, case_id, active=True)
    await require_treating_doctor(session, actor, case.patient_id)
    visit = await session.get(Visit, visit_id, with_for_update=True)
    if visit is None or visit.case_id != case.id:
        raise NotFoundError("Приём не найден")
    if visit.content.get("status") == "completed":
        raise InvalidStateError("Завершённый приём сохраняется в истории и не перезаписывается")
    validate_encounter(content)
    await _validate_encounter_references(session, case.id, content)
    visit.kind, visit.held_at, visit.content = kind, held_at, content
    await audit.record(
        session,
        actor,
        "visit.updated",
        entity_type="visit",
        entity_id=visit.id,
        patient_id=case.patient_id,
        details={"status": content.get("status", "draft")},
        now=now,
    )
    await session.flush()
    return visit


async def _validate_encounter_references(session: AsyncSession, case_id: uuid.UUID, content: dict[str, object]) -> None:
    if content.get("schema") != "encounter-v1" or content.get("status") != "completed":
        return
    diagnosis = await main_diagnosis(session, case_id)
    if diagnosis is None or diagnosis.icd_code != content.get("diagnosis"):
        raise InvalidStateError("Основной диагноз изменился: обновите приём")
    if content.get("action") != "continue":
        from kompas.modules.therapy.models import Prescription

        try:
            prescription_id = uuid.UUID(str(content.get("prescriptionId")))
        except ValueError:
            raise InvalidStateError("Не найдено подтверждённое назначение") from None
        prescription = await session.get(Prescription, prescription_id)
        if prescription is None or prescription.case_id != case_id:
            raise InvalidStateError("Назначение не относится к этому случаю")
