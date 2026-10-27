"""Endpoints: basket, suggestions, prescriptions, patient-reported drugs, drug search."""

import uuid
from datetime import date, datetime
from typing import Any

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from kompas.api.deps import ActorDep, ClockDep, DoctorDep, KnowledgeDep, PatientDep, SessionDep
from kompas.core.errors import DrugDataUnavailableError
from kompas.core.jsonable import to_jsonable
from kompas.json_input import parse_draft
from kompas.modules.identity.schemas import Page
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.drugs import DrugCatalog
from kompas.modules.therapy import service
from kompas.modules.therapy.domain.basket import Draft
from kompas.modules.therapy.domain.checks import CheckReport
from kompas.modules.therapy.domain.selection import diagnosis_rows
from kompas.modules.therapy.domain.selection_form import SelectionAnswers, form_options, indication_flags, stage_rows
from kompas.modules.therapy.models import Prescription, PrescriptionItem

router = APIRouter(tags=["therapy"])


class DraftIn(BaseModel):
    """The browser's draft: ``new_items`` = [{key, label_id, regimen, origin}], ``stopped`` = refs."""

    new_items: list[dict[str, Any]] = Field(default_factory=list)
    stopped: list[str] = Field(default_factory=list)


class ConfirmIn(BaseModel):
    draft: DraftIn
    base_token: str
    justifications: dict[str, str] = Field(default_factory=dict)  # warning id → text


class ReportedDrugIn(BaseModel):
    submission_id: uuid.UUID
    label_id: str | None = None
    substance: str | None = None


class CurrentMedicationIn(BaseModel):
    ref: str | None = None
    label_id: str | None = None
    substance: str | None = None


class CurrentReviewIn(BaseModel):
    base_token: str
    items: list[CurrentMedicationIn] = Field(max_length=100)


class ReportedDrugOut(BaseModel):
    drug_id: uuid.UUID
    label_id: str | None
    substance: str | None
    created_at: datetime


class PrescriptionLineOut(BaseModel):
    trade_name: str
    form: str
    intakes: list[dict[str, Any]]
    as_needed: str | None
    start: date
    end: date


class PrescriptionOut(BaseModel):
    prescription_id: uuid.UUID
    version: int
    confirmed_at: datetime
    knowledge_version: str
    items: list[dict[str, Any]]
    stopped_course_ids: list[str]
    warnings: list[dict[str, Any]]
    justifications: list[dict[str, str]]

    @classmethod
    def of(cls, p: Prescription, items: list[PrescriptionItem]) -> "PrescriptionOut":
        return cls(
            prescription_id=p.id,
            version=p.version,
            confirmed_at=p.confirmed_at,
            knowledge_version=p.knowledge_version,
            items=[
                {
                    "course_id": str(i.course_id),
                    "label_id": i.label_id,
                    "substance": i.substance,
                    "trade_name": i.trade_name,
                    "form": i.form,
                    "regimen": i.regimen,
                    "is_new": i.is_new,
                    "provenance": i.provenance,
                    "suggested_dose_text": i.suggested_dose_text,
                }
                for i in items
            ],
            stopped_course_ids=p.stopped_course_ids,
            warnings=p.warnings,
            justifications=p.justifications,
        )


def _catalog(knowledge: Knowledge) -> DrugCatalog:
    if knowledge.drugs is None:
        raise DrugDataUnavailableError("База препаратов (ОХЛП) не загружена")
    return knowledge.drugs


def _draft(body: DraftIn, knowledge: Knowledge) -> Draft:
    return parse_draft(body.model_dump(), _catalog(knowledge))


def _report(report: CheckReport) -> dict[str, Any]:
    data: dict[str, Any] = to_jsonable(report)
    data["boxes"] = [
        {"key": target.item_key, "part": target.part.value, "severity": severity.value}
        for target, severity in report.boxes().items()
    ]
    return data


@router.get("/patients/{patient_id}/therapy/current")
async def get_current(
    patient_id: uuid.UUID, actor: DoctorDep, session: SessionDep, knowledge: KnowledgeDep
) -> dict[str, Any]:
    """GET /patients/{id}/therapy/current — CurrentTherapy (opens the basket, D-041)."""
    current = await service.current_therapy(session, actor, patient_id)
    return current_response(current, knowledge)


def current_response(current: service.CurrentTherapy, knowledge: Knowledge) -> dict[str, Any]:
    items = to_jsonable(list(current.items))
    for row in items:
        if row["label_id"] and knowledge.drugs is not None:
            row["substance"] = " + ".join(knowledge.drugs.get(row["label_id"]).substances)
    return {"items": items, "base_token": current.base_token}


@router.put("/patients/{patient_id}/therapy/current")
async def put_current(
    patient_id: uuid.UUID,
    body: CurrentReviewIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> dict[str, Any]:
    current = await service.reconcile_current(
        session,
        actor,
        knowledge,
        patient_id,
        base_token=body.base_token,
        rows=[row.model_dump() for row in body.items],
        now=clock.now(),
    )
    return current_response(current, knowledge)


@router.post("/patients/{patient_id}/basket/check")
async def post_check(
    patient_id: uuid.UUID,
    body: DraftIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> dict[str, Any]:
    """POST /patients/{id}/basket/check — draft → CheckReport."""
    report = await service.check(session, actor, knowledge, patient_id, _draft(body, knowledge), now=clock.now())
    return _report(report)


@router.post("/patients/{patient_id}/basket/confirm", status_code=201)
async def post_confirm(
    patient_id: uuid.UUID,
    body: ConfirmIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> PrescriptionOut:
    """POST /patients/{id}/basket/confirm — draft + justifications + base_token;
    422 ``justification_required`` drives the UI's second confirmation; 409 if the base is stale
    (reload current therapy and re-check)."""
    prescription = await service.confirm(
        session,
        actor,
        knowledge,
        patient_id,
        _draft(body.draft, knowledge),
        body.justifications,
        base_token=body.base_token,
        now=clock.now(),
    )
    items = await service.items_of(session, [prescription.id])
    return PrescriptionOut.of(prescription, items[prescription.id])


@router.get("/patients/{patient_id}/suggestions")
async def get_suggestions(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    choice: str | None = None,
    flag: list[str] = Query(default=[]),
) -> dict[str, Any]:
    """GET /patients/{id}/suggestions — every КР row of the main diagnosis with its labels; no stage (D-122)."""
    selection = await service.suggestions(session, actor, knowledge, patient_id, SelectionAnswers(choice, tuple(flag)))
    data: dict[str, Any] = to_jsonable(selection)
    return data


@router.get("/patients/{patient_id}/prescriptions")
async def get_prescriptions(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> Page[PrescriptionOut]:
    """GET /patients/{id}/prescriptions (doctor, paginated) · GET /me/prescription (patient lines)."""
    rows, total = await service.doctor_history(session, actor, patient_id, offset=offset, limit=limit)
    items = await service.items_of(session, [p.id for p in rows])
    return Page(items=[PrescriptionOut.of(p, items[p.id]) for p in rows], total=total, offset=offset, limit=limit)


@router.get("/me/prescription")
async def get_my_prescription(
    actor: PatientDep, session: SessionDep, knowledge: KnowledgeDep
) -> list[PrescriptionLineOut]:
    """GET /me/prescription — brand, dose and times only (D-051)."""
    lines = await service.my_prescription(session, actor, knowledge)
    return [
        PrescriptionLineOut(
            trade_name=line.trade_name,
            form=line.form,
            intakes=[{"at": at.strftime("%H:%M"), "amount_mg": float(mg)} for at, mg in line.intakes],
            as_needed=line.as_needed,
            start=line.start,
            end=line.end,
        )
        for line in lines
    ]


@router.post("/me/drugs", status_code=201)
async def post_reported_drug(
    body: ReportedDrugIn, actor: PatientDep, session: SessionDep, clock: ClockDep
) -> ReportedDrugOut:
    """POST /me/drugs — patient-reported drug (with submission_id)."""
    drug = await service.report_drug(
        session,
        actor,
        submission_id=body.submission_id,
        label_id=body.label_id,
        substance=body.substance,
        now=clock.now(),
    )
    return ReportedDrugOut(
        drug_id=drug.id, label_id=drug.label_id, substance=drug.substance, created_at=drug.created_at
    )


@router.get("/therapy/frequent")
async def get_frequent(
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
    patient_id: uuid.UUID | None = None,
    days: int = Query(180, ge=7, le=730),
    limit: int = Query(8, ge=1, le=30),
) -> list[dict[str, Any]]:
    """GET /therapy/frequent?patient_id=&days=&limit= — «Вы часто назначаете»: ОХЛП records this doctor
    most often added in confirmed prescriptions; with ``patient_id``, each carries the КР status for
    that patient's main diagnosis (as in the basket). Warnings come from the basket check as usual."""
    picks = await service.frequent_picks(
        session, actor, knowledge, patient_id=patient_id, days=days, limit=limit, now=clock.now()
    )
    return [
        {
            "label_id": p.label.id,
            "display_name": p.label.display_name,
            "trade_name": p.label.trade_name,
            "form": p.label.form,
            "substances": list(p.label.substances),
            "atc": p.label.atc,
            "times": p.times,
            "last_prescribed_at": p.last_prescribed_at.isoformat(),
            "provenance": p.provenance,
        }
        for p in picks
    ]


@router.get("/drugs/vocabulary")
async def get_drug_vocabulary(knowledge: KnowledgeDep, _actor: DoctorDep) -> dict[str, Any]:
    """GET /drugs/vocabulary — doctor: МНН, trade names (with their МНН) and excipients from the ОХЛП base,
    for the profile lists «Непереносимость», «Вспомогательные вещества», «Неэффективность в прошлом»."""
    vocabulary = _catalog(knowledge).vocabulary()
    return {
        "substances": list(vocabulary.substances),
        "trade_names": [{"name": name, "substances": list(subs)} for name, subs in vocabulary.trade_names],
        "excipients": list(vocabulary.excipients),
        "aliases": knowledge.guidelines.vocabulary.substance_aliases,
    }


@router.get("/medications/search")
async def search_current_medications(
    knowledge: KnowledgeDep, _actor: DoctorDep, q: str = "", limit: int = Query(50, ge=1, le=500)
) -> dict[str, Any]:
    """All CSV names for actual medication use, including non-psychiatric drugs."""
    from kompas.core.text import normalize_name

    canonical = knowledge.guidelines.vocabulary.substance_aliases.get(normalize_name(q), q)
    items, total = _catalog(knowledge).search_medications(canonical, limit=limit)
    return {"items": items, "total": total}


@router.get("/drugs/forms")
async def get_drug_forms(knowledge: KnowledgeDep, _actor: ActorDep) -> list[str]:
    """Available dosage forms for filtering the complete catalog."""
    return list(_catalog(knowledge).forms())


@router.get("/drugs/{label_id}")
async def get_drug(label_id: str, knowledge: KnowledgeDep, _actor: DoctorDep) -> dict[str, Any]:
    """GET /drugs/{label_id} — doctor: one label with its facts, quotes and sections."""
    label = _catalog(knowledge).get(label_id)
    data: dict[str, Any] = to_jsonable(label)
    data["display_name"] = label.display_name
    return data


@router.get("/drugs")
async def get_drug_search(
    knowledge: KnowledgeDep,
    _actor: ActorDep,  # patients search too, to report what they take
    q: str = Query(""),
    atc: str = "",
    form: str = "",
    mnn: str = "",
    substance: str = Query(""),
    trade_name: str = Query(""),
    forms: list[str] | None = Query(None),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
) -> dict[str, Any]:
    """GET /drugs?q=&offset=&limit= — label search; empty q lists all labels by МНН (503 if drug data isn't loaded)."""
    catalog = _catalog(knowledge)
    page = catalog.search(q, substance=substance, trade_name=trade_name, forms=forms, offset=0, limit=len(catalog))
    matching = [
        label
        for label in page.items
        if (not atc or (label.atc or "").casefold().startswith(atc.strip().casefold()))
        and (not form or form.strip().casefold() in label.form.casefold())
        and (not mnn or any(mnn.strip().casefold() in s.casefold() for s in label.substances))
    ]
    return {
        "items": [
            {
                "label_id": label.id,
                "display_name": label.display_name,
                "trade_name": label.trade_name,
                "form": label.form,
                "substances": list(label.substances),
                "atc": label.atc,
            }
            for label in matching[offset : offset + limit]
        ],
        "total": len(matching),
        "offset": offset,
        "limit": limit,
    }


@router.get("/guidelines/selection-form")
async def selection_form(
    diagnosis: str, knowledge: KnowledgeDep, _actor: DoctorDep, choice: str | None = None
) -> dict[str, Any]:
    from kompas.core.icd import IcdCode

    code = IcdCode.parse(diagnosis)
    rows = diagnosis_rows(code, knowledge.guidelines)
    return {**form_options(code, rows), "flags": indication_flags(code, stage_rows(code, rows, choice))}
