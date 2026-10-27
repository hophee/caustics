"""Therapy use cases: current therapy, stateless basket checks, confirmation, suggestions, history.

The browser holds the draft (D-040); the server supplies current therapy (D-061), runs
``checks.check_basket`` on every check and again inside ``confirmation.confirm``, computes provenance
itself and stores only confirmed versions. A draft is based on what the doctor saw (``base_token``:
latest version + open patient-reported drugs); confirming on top of anything newer is refused (409),
so the doctor never confirms a regimen or a reported drug they haven't seen. The case row is locked
during confirmation (serialises concurrent confirms and confirm vs close).
"""

import hashlib
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core import outbox
from kompas.core.actor import Actor
from kompas.core.clock import local_date
from kompas.core.errors import (
    DrugDataUnavailableError,
    InvalidInputError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    Problem,
)
from kompas.core.icd import IcdCode
from kompas.core.jsonable import to_jsonable
from kompas.core.text import normalize_name
from kompas.json_input import parse_regimen
from kompas.modules.audit import service as audit
from kompas.modules.cases.models import CaseStatus, MedicalCase
from kompas.modules.cases.service import main_diagnosis, mark_medications_reviewed, profile_of, require_treating_doctor
from kompas.modules.identity.models import Patient
from kompas.modules.identity.service import ensure_patient_access, treating_doctor
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.drugs import DrugCatalog, DrugLabel
from kompas.modules.notifications import service as notifications
from kompas.modules.therapy.domain import confirmation
from kompas.modules.therapy.domain.basket import (
    CURRENT_PREFIX,
    CurrentTherapyItem,
    DailySchedule,
    DoseRegimen,
    Draft,
    PrnSchedule,
    Search,
)
from kompas.modules.therapy.domain.checks import CheckContext, CheckReport, check_basket
from kompas.modules.therapy.domain.selection import Selection, provenance, select_candidates
from kompas.modules.therapy.domain.selection_form import SelectionAnswers
from kompas.modules.therapy.models import CurrentTherapyReview, PatientReportedDrug, Prescription, PrescriptionItem

TOPIC = "prescription.confirmed"
REPORTED_PREFIX = "reported:"
ANTIDEPRESSANT_ATC = "N06A"


def regimen_json(regimen: DoseRegimen | None) -> dict[str, Any] | None:
    """Store a regimen in the same JSON shape the API accepts (``json_input.parse_regimen``), in mg."""
    if regimen is None:
        return None
    data: dict[str, Any] = {"start": regimen.start.isoformat(), "duration_days": regimen.duration_days, "unit": "мг"}
    if isinstance(regimen.schedule, PrnSchedule):
        data["prn"] = {"dose": str(regimen.schedule.dose_mg), "max_per_day": regimen.schedule.max_doses_per_day}
    else:
        data["intakes"] = [{"at": i.at.strftime("%H:%M"), "amount": str(i.amount_mg)} for i in regimen.schedule.intakes]
    return data


def _regimen(data: dict[str, Any] | None, pointer: str) -> DoseRegimen | None:
    return parse_regimen(data, pointer) if data is not None else None


def _catalog(knowledge: Knowledge) -> DrugCatalog:
    if knowledge.drugs is None:
        raise DrugDataUnavailableError("База препаратов (ОХЛП) не загружена")
    return knowledge.drugs


async def _active_case(session: AsyncSession, patient_id: uuid.UUID, *, lock: bool = False) -> MedicalCase:
    case = await _optional_case(session, patient_id, lock=lock)
    if case is None:
        raise InvalidStateError("У пациента нет активного случая")
    return case


async def _optional_case(session: AsyncSession, patient_id: uuid.UUID, *, lock: bool = False) -> MedicalCase | None:
    query = select(MedicalCase).where(
        MedicalCase.patient_id == patient_id, MedicalCase.status == CaseStatus.ACTIVE, MedicalCase.demo.is_(False)
    )
    if lock:
        query = query.with_for_update().execution_options(populate_existing=True)
    return await session.scalar(query)


async def _latest(session: AsyncSession, case_id: uuid.UUID) -> Prescription | None:
    return await session.scalar(
        select(Prescription).where(Prescription.case_id == case_id).order_by(Prescription.version.desc()).limit(1)
    )


async def items_of(session: AsyncSession, prescription_ids: list[uuid.UUID]) -> dict[uuid.UUID, list[PrescriptionItem]]:
    """Items per prescription (for history views)."""
    grouped: dict[uuid.UUID, list[PrescriptionItem]] = {pid: [] for pid in prescription_ids}
    rows = await session.scalars(select(PrescriptionItem).where(PrescriptionItem.prescription_id.in_(prescription_ids)))
    for item in rows:
        grouped[item.prescription_id].append(item)
    return grouped


async def _open_reported(session: AsyncSession, patient_id: uuid.UUID) -> list[PatientReportedDrug]:
    rows = await session.scalars(
        select(PatientReportedDrug)
        .where(
            PatientReportedDrug.patient_id == patient_id,
            PatientReportedDrug.confirmed_at.is_(None),
            PatientReportedDrug.removed_at.is_(None),
        )
        .order_by(PatientReportedDrug.created_at)
    )
    return list(rows.all())


def _token(latest: Prescription | None, reported: list[PatientReportedDrug]) -> str:
    parts = [str(latest.id) if latest else "none", *sorted(str(r.id) for r in reported)]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()[:32]


async def _current(
    session: AsyncSession, patient_id: uuid.UUID, case: MedicalCase | None
) -> tuple[tuple[CurrentTherapyItem, ...], Prescription | None, list[PatientReportedDrug], str]:
    latest = await _latest(session, case.id) if case else None
    items: list[CurrentTherapyItem] = []
    if latest is not None:
        for row in (await items_of(session, [latest.id]))[latest.id]:
            items.append(
                CurrentTherapyItem(
                    ref=str(row.course_id),
                    label_id=row.label_id,
                    substance=row.substance,
                    regimen=_regimen(row.regimen, f"current[{row.course_id}].regimen"),
                )
            )
    reported = await _open_reported(session, patient_id)
    review = await session.scalar(
        select(CurrentTherapyReview)
        .where(
            CurrentTherapyReview.patient_id == patient_id,
            CurrentTherapyReview.case_id == (case.id if case else None),
            CurrentTherapyReview.prescription_id == (latest.id if latest else None),
        )
        .order_by(CurrentTherapyReview.created_at.desc(), CurrentTherapyReview.id.desc())
        .limit(1)
    )
    if review is not None:
        items = [
            CurrentTherapyItem(
                ref=row["ref"],
                label_id=row["label_id"],
                substance=row["substance"],
                regimen=_regimen(row["regimen"], "review.regimen"),
                patient_reported=row["patient_reported"],
            )
            for row in review.items
        ]
    for drug in reported:
        if review is not None and str(drug.id) in review.reviewed_reported_ids:
            continue
        items.append(
            CurrentTherapyItem(
                ref=f"{REPORTED_PREFIX}{drug.id}",
                label_id=drug.label_id,
                substance=drug.substance,
                regimen=_regimen(drug.regimen, f"reported[{drug.id}].regimen"),
                patient_reported=True,
            )
        )
    token = _token(latest, reported)
    if review is not None:
        token = hashlib.sha256(f"{token}|{review.id}".encode()).hexdigest()[:32]
    return tuple(items), latest, reported, token


async def reconcile_current(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    *,
    base_token: str,
    rows: list[dict[str, Any]],
    now: datetime,
) -> "CurrentTherapy":
    """Record actual use by canonical MNN; lock and audit, preserving prescription history."""
    await session.get(Patient, patient_id, with_for_update=True, populate_existing=True)
    await require_treating_doctor(session, actor, patient_id)
    case = await _optional_case(session, patient_id, lock=True)
    current, latest, reported, token = await _current(session, patient_id, case)
    if token != base_token:
        raise InvalidStateError("Текущая терапия изменилась. Обновите список перед сохранением.")
    catalog = _catalog(knowledge)
    vocabulary = catalog.medication_substances()
    existing = {item.ref: item for item in current}
    saved: list[dict[str, Any]] = []
    seen_refs: set[str] = set()
    for row in rows:
        ref = row.get("ref")
        if ref and (ref not in existing or ref in seen_refs):
            raise InvalidInputError("Препарат в текущей терапии изменился или указан дважды")
        if ref:
            seen_refs.add(ref)
        label_id = row.get("label_id")
        if label_id:
            label = catalog.get(label_id)
            substances = label.substances
        else:
            selected = normalize_name(row.get("substance") or "")
            selected = knowledge.guidelines.vocabulary.substance_aliases.get(selected, selected)
            if selected not in vocabulary:
                raise InvalidInputError("Выберите МНН или торговое название из базы ОХЛП")
            substances = (vocabulary[selected],)
        if not substances:
            raise InvalidInputError("В записи ОХЛП не указано действующее вещество")
        substance = " + ".join(substances)
        previous = existing.get(ref)
        same = (
            previous is not None
            and previous.label_id == label_id
            and (bool(label_id) or normalize_name(previous.substance or "") == normalize_name(substance))
        )
        saved.append(
            {
                "ref": previous.ref if same else str(uuid.uuid4()),
                "label_id": label_id,
                "substance": substance,
                "regimen": regimen_json(previous.regimen) if same else None,
                "patient_reported": previous.patient_reported if same else False,
            }
        )
    previous_time = await session.scalar(
        select(func.max(CurrentTherapyReview.created_at)).where(
            CurrentTherapyReview.patient_id == patient_id,
        )
    )
    recorded_at = max(now, previous_time + timedelta(microseconds=1)) if previous_time else now
    kept_refs = {row["ref"] for row in saved}
    for drug in reported:
        if f"{REPORTED_PREFIX}{drug.id}" not in kept_refs:
            drug.removed_at = now
    review = CurrentTherapyReview(
        id=uuid.uuid4(),
        created_at=recorded_at,
        patient_id=patient_id,
        case_id=case.id if case else None,
        prescription_id=latest.id if latest else None,
        recorded_by=actor.user_id,
        items=saved,
        reviewed_reported_ids=[str(r.id) for r in reported],
    )
    session.add(review)
    await session.flush()
    await mark_medications_reviewed(session, actor, patient_id, now=now)
    await audit.record(
        session,
        actor,
        "therapy.reconciled",
        entity_type="current_therapy_review",
        entity_id=review.id,
        patient_id=patient_id,
        details={"items": saved, "previous_refs": list(existing)},
        now=now,
    )
    result, _, _, updated_token = await _current(session, patient_id, case)
    return CurrentTherapy(result, updated_token)


async def _context(session: AsyncSession, knowledge: Knowledge, access: Any, now: datetime) -> CheckContext:
    profile = await profile_of(session, access)
    return CheckContext(
        knowledge.version,
        profile,
        _catalog(knowledge),
        knowledge.check_rules,
        knowledge.interaction_overrides,
        local_date(now, profile.timezone),
        # Project specification, pp. 16–18: investigations are recorded, but do not
        # participate in medication filtering in this prototype.
    )


async def on_antidepressant(session: AsyncSession, case_id: uuid.UUID, knowledge: Knowledge) -> bool:
    """Whether the current regimen of a case contains an antidepressant (ATC N06A). Without the drug
    catalog any current therapy counts as "yes" (safer: triage rules then also apply)."""
    case = await session.get(MedicalCase, case_id)
    if case is None:
        return False
    items, _, _, _ = await _current(session, case.patient_id, case)
    if not items:
        return False
    catalog = knowledge.drugs
    if catalog is None:
        return True
    for item in items:
        labels = [catalog.get(item.label_id)] if item.label_id else list(catalog.by_substance(item.substance or ""))
        if any((label.atc or "").startswith(ANTIDEPRESSANT_ATC) for label in labels):
            return True
    return False


@dataclass(frozen=True, slots=True)
class CurrentTherapy:
    """What opens the basket: items (refs = course ids or ``reported:<id>``) and ``base_token`` =
    hash(latest prescription id + sorted open patient-reported drug ids). The browser sends the token
    back on confirm; any change in what the doctor saw (new version **or** a newly reported drug)
    makes it stale."""

    items: tuple[CurrentTherapyItem, ...]
    base_token: str


@dataclass(frozen=True, slots=True)
class PatientPrescriptionLine:
    """Patient view of one drug (D-051): brand, dose, times — no warnings, provenance or suggestions."""

    trade_name: str
    form: str
    intakes: tuple[tuple[time, Decimal], ...]  # (local time, mg); empty for "as needed"
    as_needed: str | None  # e.g. "0.5 мг, не больше 3 раз в сутки"
    start: date
    end: date


async def current_therapy(session: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> CurrentTherapy:
    """Latest version's items of the active case plus open patient-reported drugs."""
    access = await ensure_patient_access(session, actor, patient_id)
    items, _, _, token = await _current(session, access.patient.id, await _optional_case(session, patient_id))
    return CurrentTherapy(items, token)


async def check(
    session: AsyncSession, actor: Actor, knowledge: Knowledge, patient_id: uuid.UUID, draft: Draft, *, now: datetime
) -> CheckReport:
    """Treating doctor's live check of a browser draft. Nothing is stored.

    Raises:
        NotFoundError: no access. DrugDataUnavailableError (503): drug catalog not loaded.
    """
    access = await require_treating_doctor(session, actor, patient_id)
    ctx = await _context(session, knowledge, access, now)
    current, _, _, _ = await _current(session, patient_id, await _active_case(session, patient_id))
    return check_basket(current, draft, ctx)


async def confirm(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    draft: Draft,
    justifications: Mapping[str, str],
    *,
    base_token: str,
    now: datetime,
) -> Prescription:
    """Lock the case → recompute the base token and compare (409 if a new version exists or the set
    of open patient-reported drugs changed) → provenance per new item (main
    diagnosis) → ``previously_justified`` from the base version → ``confirmation.confirm`` → store the
    version (course ids kept for continued drugs, new ones for new drugs; reported drugs kept are
    marked confirmed, stopped ones removed) → audit (justified red warnings with id and text, D-046)
    → outbox ``prescription.confirmed`` (patient notification).

    Raises:
        InvalidInputError: confirmation problems (``justification_required`` with warning id pointer).
        InvalidStateError: stale base version (409), no active case, or no main diagnosis.
    """
    access = await require_treating_doctor(session, actor, patient_id)
    catalog = _catalog(knowledge)
    case = await _active_case(session, patient_id, lock=True)
    current, latest, reported, token = await _current(session, patient_id, case)
    if token != base_token:
        raise InvalidStateError(
            "Текущая терапия изменилась — обновите корзину и проверьте назначение заново",
            (Problem("stale_base", "Терапия изменилась с момента открытия корзины", "base_token"),),
        )
    main = await main_diagnosis(session, case.id)
    if main is None:
        raise InvalidStateError("Сначала укажите основной диагноз случая")
    diagnosis = IcdCode(main.icd_code, main.icd_original)
    ctx = await _context(session, knowledge, access, now)
    provenances = {
        item.key: provenance(item.origin, catalog.get(item.label_id), diagnosis, knowledge.guidelines)
        for item in draft.new_items
    }
    previously = frozenset(latest.justified_warning_ids) if latest else frozenset()
    snapshot = confirmation.confirm(
        current, draft, ctx, justifications, previously, provenances, doctor_id=str(actor.doctor_id), now=now
    )
    prescription = Prescription(
        id=uuid.uuid4(),
        created_at=now,
        case_id=case.id,
        version=(latest.version + 1) if latest else 1,
        previous_id=latest.id if latest else None,
        confirmed_by=actor.user_id,
        confirmed_at=now,
        knowledge_version=snapshot.knowledge_version,
        profile_snapshot=to_jsonable(ctx.patient),
        warnings=[to_jsonable(w) for w in snapshot.warnings_at_confirmation],
        justifications=[{"warning_id": wid, "text": text} for wid, text in snapshot.justifications],
        justified_warning_ids=sorted(snapshot.justified_warning_ids),
        stopped_course_ids=sorted(draft.stopped),
    )
    session.add(prescription)
    await session.flush()
    reported_by_ref = {f"{REPORTED_PREFIX}{r.id}": r for r in reported}
    for confirmed in snapshot.items:
        item = confirmed.item
        course_id, reported_drug = uuid.uuid4(), None
        if not item.is_new:
            ref = item.key.removeprefix(CURRENT_PREFIX)
            reported_drug = reported_by_ref.get(ref)
            if reported_drug is None:
                course_id = uuid.UUID(ref)
            else:
                reported_drug.confirmed_at = now
        label = catalog.get(item.label_id) if item.label_id else None
        session.add(
            PrescriptionItem(
                id=uuid.uuid4(),
                prescription_id=prescription.id,
                course_id=course_id,
                label_id=item.label_id,
                substance=item.substance or (label.substances[0] if label and label.substances else None),
                trade_name=label.trade_name if label else (item.substance or "препарат"),
                form=label.form if label else "",
                regimen=regimen_json(item.regimen),
                is_new=item.is_new,
                reported_drug_id=reported_drug.id if reported_drug else None,
                provenance=to_jsonable(confirmed.provenance) if confirmed.provenance else None,
                suggested_dose_text=confirmed.suggested_dose_text,
            )
        )
    for ref in draft.stopped:
        if ref in reported_by_ref:
            reported_by_ref[ref].removed_at = now
    await audit.record(
        session,
        actor,
        "prescription.confirmed",
        entity_type="prescription",
        entity_id=prescription.id,
        patient_id=patient_id,
        details={
            "version": prescription.version,
            "warnings": len(prescription.warnings),
            "justified": prescription.justifications,
        },
        now=now,
    )
    await outbox.enqueue(
        session, TOPIC, {"prescription_id": str(prescription.id), "patient_id": str(patient_id)}, now=now
    )
    await session.flush()
    return prescription


async def doctor_history(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, *, offset: int, limit: int
) -> tuple[list[Prescription], int]:
    """All versions with snapshots, newest first (doctor)."""
    await require_treating_doctor(session, actor, patient_id)
    where = MedicalCase.patient_id == patient_id
    base = select(Prescription).join(MedicalCase, MedicalCase.id == Prescription.case_id).where(where)
    total = await session.scalar(select(func.count()).select_from(base.subquery()))
    rows = await session.scalars(
        base.order_by(Prescription.confirmed_at.desc(), Prescription.version.desc()).offset(offset).limit(limit)
    )
    return list(rows.all()), total or 0


async def my_prescription(session: AsyncSession, actor: Actor, knowledge: Knowledge) -> list[PatientPrescriptionLine]:
    """Patient: the current regimen only, as plain lines (D-051), from the names snapshotted on the
    prescription items (works without the drug catalog)."""
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    try:
        case = await _active_case(session, actor.patient_id)
    except InvalidStateError:
        return []
    latest = await _latest(session, case.id)
    if latest is None:
        return []
    return await prescription_lines(session, latest.id)


async def prescription_lines(session: AsyncSession, prescription_id: uuid.UUID) -> list[PatientPrescriptionLine]:
    """One prescription version as patient-facing lines (D-051); also used for the prescription document."""
    lines = []
    for item in (await items_of(session, [prescription_id])).get(prescription_id, []):
        regimen = _regimen(item.regimen, "regimen")
        if regimen is None:
            continue
        if isinstance(regimen.schedule, DailySchedule):
            intakes = tuple((i.at, i.amount_mg) for i in regimen.schedule.intakes)
            as_needed = None
        else:
            intakes = ()
            dose = f"{regimen.schedule.dose_mg.normalize():f}"
            as_needed = f"{dose} мг, не больше {regimen.schedule.max_doses_per_day} раз в сутки"
        lines.append(
            PatientPrescriptionLine(item.trade_name, item.form, intakes, as_needed, regimen.start, regimen.end)
        )
    return lines


async def report_drug(
    session: AsyncSession,
    actor: Actor,
    *,
    submission_id: uuid.UUID,
    label_id: str | None,
    substance: str | None,
    now: datetime,
) -> PatientReportedDrug:
    """Patient adds a drug they take (D-050); the doctor gets a notification; audited."""
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    existing = await session.scalar(
        select(PatientReportedDrug).where(
            PatientReportedDrug.patient_id == actor.patient_id, PatientReportedDrug.submission_id == submission_id
        )
    )
    if existing is not None:
        return existing
    if not label_id and not (substance or "").strip():
        raise InvalidInputError(
            "Укажите препарат из списка или его название", (Problem("invalid_input", "Нет препарата", "substance"),)
        )
    drug = PatientReportedDrug(
        id=uuid.uuid4(),
        created_at=now,
        submission_id=submission_id,
        patient_id=actor.patient_id,
        label_id=label_id,
        substance=(substance or "").strip() or None,
        regimen=None,
        confirmed_at=None,
        removed_at=None,
    )
    session.add(drug)
    await session.flush()
    doctor = await treating_doctor(session, actor.patient_id)  # share-locks the patient (transfer race)
    if doctor is not None:
        await notifications.notify(
            session,
            doctor.user_id,
            "reported_drug",
            {"patient_id": str(actor.patient_id), "drug_id": str(drug.id)},
            now=now,
            patient_id=actor.patient_id,
        )
    await audit.record(
        session,
        actor,
        "drug.reported",
        entity_type="patient_reported_drug",
        entity_id=drug.id,
        patient_id=actor.patient_id,
        details={"label_id": label_id},
        now=now,
    )
    return drug


@dataclass(frozen=True, slots=True)
class FrequentPick:
    """An ОХЛП record the doctor often prescribes («Вы часто назначаете», the drug picker's empty state).
    ``provenance`` is filled when a patient is given: the КР status for that patient's main diagnosis."""

    label: DrugLabel
    times: int
    last_prescribed_at: datetime
    provenance: dict[str, Any] | None


async def frequent_picks(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    *,
    patient_id: uuid.UUID | None,
    days: int,
    limit: int,
    now: datetime,
) -> list[FrequentPick]:
    """The labels this doctor most often added as *new* drugs in confirmed prescriptions over the last
    ``days`` (most frequent first, then most recent). Drugs carried over unchanged don't count; labels
    missing from the loaded catalog are skipped."""
    if not actor.is_doctor:
        raise PermissionDeniedError("Действие доступно только врачу")
    diagnosis = None
    if patient_id is not None:
        await require_treating_doctor(session, actor, patient_id)
        case = await _active_case(session, patient_id)
        main = await main_diagnosis(session, case.id)
        diagnosis = IcdCode(main.icd_code, main.icd_original) if main else None
    catalog = _catalog(knowledge)
    times = func.count(PrescriptionItem.id)
    last = func.max(Prescription.confirmed_at)
    rows = await session.execute(
        select(PrescriptionItem.label_id, times, last)
        .join(Prescription, Prescription.id == PrescriptionItem.prescription_id)
        .where(
            Prescription.confirmed_by == actor.user_id,
            Prescription.confirmed_at >= now - timedelta(days=days),
            PrescriptionItem.is_new.is_(True),
            PrescriptionItem.label_id.is_not(None),
        )
        .group_by(PrescriptionItem.label_id)
        .order_by(times.desc(), last.desc(), PrescriptionItem.label_id)
        .limit(limit * 2)  # head-room for labels no longer in the catalog
    )
    picks = []
    for label_id, count, last_at in rows.all():
        if label_id is None:  # excluded by the query; narrows the type
            continue
        try:
            label = catalog.get(label_id)
        except NotFoundError:
            continue
        found = provenance(Search(), label, diagnosis, knowledge.guidelines) if diagnosis else None
        picks.append(FrequentPick(label, count, last_at, to_jsonable(found) if found else None))
    return picks[:limit]


async def suggestions(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    answers: SelectionAnswers | None = None,
) -> Selection:
    """Guideline suggestions for the active case's main diagnosis and the patient's profile."""
    access = await require_treating_doctor(session, actor, patient_id)
    catalog = _catalog(knowledge)
    case = await _active_case(session, patient_id)
    main = await main_diagnosis(session, case.id)
    if main is None:
        raise InvalidStateError("Сначала укажите основной диагноз случая")
    profile = await profile_of(session, access)
    return select_candidates(
        IcdCode(main.icd_code, main.icd_original), knowledge.guidelines, catalog, profile, answers=answers
    )
