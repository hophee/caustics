"""Patient's own words → treating doctor's form. No inferred severity or causality."""

import uuid

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import select, text

from kompas.api.deps import ClockDep, DoctorDep, PatientDep, SessionDep
from kompas.core.errors import InvalidInputError, NotFoundError
from kompas.core.security import Role
from kompas.modules.adverse_events.models import AdverseEvent, AeStatus
from kompas.modules.adverse_events.service import _active_case, _doctor_event
from kompas.modules.audit import service as audit
from kompas.modules.cases.service import get_profile
from kompas.modules.identity.models import Doctor, Patient
from kompas.modules.notifications import service as notifications

router = APIRouter()


class StartIn(BaseModel):
    submission_id: uuid.UUID


class DetailsIn(BaseModel):
    trade_name: str = Field(min_length=1, max_length=300)
    description: str = Field(min_length=1, max_length=5000)
    onset_text: str = Field(min_length=1, max_length=500)

    @field_validator("trade_name", "description", "onset_text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("Заполните поле")
        return value  # Preserve the patient's text, including whitespace, verbatim.


class SavedOut(BaseModel):
    adverse_event_id: uuid.UUID
    details_complete: bool


class FormIn(BaseModel):
    fields: dict[str, str] = Field(max_length=100)

    @field_validator("fields")
    @classmethod
    def bounded_fields(cls, value: dict[str, str]) -> dict[str, str]:
        if any(len(k) > 80 or len(v) > 10000 for k, v in value.items()):
            raise ValueError("Слишком длинное поле формы")
        return value


@router.post("/me/reaction-signals", status_code=201)
async def start(body: StartIn, actor: PatientDep, session: SessionDep, clock: ClockDep) -> SavedOut:
    # Serialize retries/double clicks for the same submission, including concurrent requests.
    await session.execute(
        text("SELECT pg_advisory_xact_lock(hashtext(:key))"), {"key": f"reaction:{actor.user_id}:{body.submission_id}"}
    )
    event = await session.scalar(
        select(AdverseEvent).where(
            AdverseEvent.reported_by == actor.user_id, AdverseEvent.submission_id == body.submission_id
        )
    )
    if event is None:
        assert actor.patient_id is not None
        case = await _active_case(session, actor.patient_id)
        event = AdverseEvent(
            id=uuid.uuid4(),
            created_at=clock.now(),
            patient_id=actor.patient_id,
            case_id=case.id,
            course_id=None,
            drug_name=None,
            symptom_code="other",
            symptom_text=None,
            severity="unassessed",
            started_on=None,
            ongoing=True,
            ended_on=None,
            reported_by=actor.user_id,
            reporter_role=Role.PATIENT,
            submission_id=body.submission_id,
            last_therapy_change_on=None,
            status=AeStatus.REPORTED,
            relation=None,
            in_label=None,
            report_required=False,
            report_sent_on=None,
            doctor_note=None,
            reviewed_by=None,
            reviewed_at=None,
            simple_report=True,
            details_complete=False,
            onset_text=None,
            report_form={},
        )
        session.add(event)
        await session.flush()
        doctor_user = await session.scalar(
            select(Doctor.user_id)
            .join(Patient, Patient.treating_doctor_id == Doctor.id)
            .where(Patient.id == actor.patient_id)
        )
        if doctor_user is not None:
            await notifications.notify(
                session,
                doctor_user,
                "adverse_event",
                {"adverse_event_id": str(event.id), "patient_id": str(actor.patient_id)},
                now=clock.now(),
                patient_id=actor.patient_id,
            )
        await audit.record(
            session,
            actor,
            "adverse_event.signal",
            entity_type="adverse_event",
            entity_id=event.id,
            patient_id=actor.patient_id,
            details={},
            now=clock.now(),
        )
    return SavedOut(adverse_event_id=event.id, details_complete=event.details_complete)


@router.get("/me/reaction-signals/pending")
async def pending(actor: PatientDep, session: SessionDep) -> list[SavedOut]:
    rows = await session.scalars(
        select(AdverseEvent)
        .where(
            AdverseEvent.patient_id == actor.patient_id,
            AdverseEvent.simple_report.is_(True),
            AdverseEvent.details_complete.is_(False),
        )
        .order_by(AdverseEvent.created_at)
    )
    return [SavedOut(adverse_event_id=e.id, details_complete=False) for e in rows]


@router.put("/me/reaction-signals/{event_id}")
async def details(
    event_id: uuid.UUID, body: DetailsIn, actor: PatientDep, session: SessionDep, clock: ClockDep
) -> SavedOut:
    event = await session.get(AdverseEvent, event_id, with_for_update=True)
    if event is None or event.patient_id != actor.patient_id or not event.simple_report:
        raise NotFoundError("Сообщение не найдено")
    # Completed patient statements are immutable; retrying the same write is safe.
    if event.details_complete:
        if (event.drug_name, event.symptom_text, event.onset_text) != (
            body.trade_name,
            body.description,
            body.onset_text,
        ):
            raise InvalidInputError("Сообщение уже сохранено. Создайте новое сообщение.")
    else:
        event.drug_name, event.symptom_text, event.onset_text = body.trade_name, body.description, body.onset_text
        event.details_complete = True
        await audit.record(
            session,
            actor,
            "adverse_event.details",
            entity_type="adverse_event",
            entity_id=event.id,
            patient_id=actor.patient_id,
            details={},
            now=clock.now(),
        )
        doctor_user = await session.scalar(
            select(Doctor.user_id)
            .join(Patient, Patient.treating_doctor_id == Doctor.id)
            .where(Patient.id == actor.patient_id)
        )
        if doctor_user is not None:
            await notifications.notify(
                session,
                doctor_user,
                "adverse_event",
                {"adverse_event_id": str(event.id), "patient_id": str(actor.patient_id)},
                now=clock.now(),
                patient_id=actor.patient_id,
            )
    return SavedOut(adverse_event_id=event.id, details_complete=True)


@router.get("/adverse-events/{event_id}/form")
async def get_form(event_id: uuid.UUID, actor: DoctorDep, session: SessionDep, clock: ClockDep) -> FormIn:
    event = await _doctor_event(session, actor, event_id)
    profile = await get_profile(session, actor, event.patient_id)
    today = clock.now().astimezone(profile.timezone).date()
    birth = profile.birth_date
    years = today.year - birth.year - ((today.month, today.day) < (birth.month, birth.day))
    defaults = {
        "sex": "Ж" if profile.sex.value == "female" else "М",
        "weight": str(profile.weight_kg) if profile.weight_kg is not None else "",
        "age": str(years),
        "pregnant": {"yes": "Есть", "no": "Нет", "unknown": "Неизвестно"}[profile.pregnant.value],
        "allergy": "Неизвестно" if profile.allergies is None else "Есть" if profile.allergies else "Нет",
        "allergy_to": "; ".join(profile.allergies or ()),
    }
    return FormIn(fields={**defaults, **event.report_form})


@router.put("/adverse-events/{event_id}/form")
async def save_form(
    event_id: uuid.UUID, body: FormIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> FormIn:
    event = await _doctor_event(session, actor, event_id)
    event.report_form = body.fields
    await audit.record(
        session,
        actor,
        "adverse_event.form_saved",
        entity_type="adverse_event",
        entity_id=event.id,
        patient_id=event.patient_id,
        details={},
        now=clock.now(),
    )
    return body
