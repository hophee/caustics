"""Endpoints: /auth/login, /auth/me, /auth/activate, /patients (doctor)."""

import uuid

from fastapi import APIRouter, Query
from pydantic import BaseModel

from kompas.api.deps import ActorDep, ClockDep, DoctorDep, PatientDep, SessionDep, SettingsDep
from kompas.core.security import Role, issue_token
from kompas.modules.identity import service
from kompas.modules.identity.models import Doctor, Patient, User
from kompas.modules.identity.schemas import (
    EmergencyContact,
    InviteIssued,
    LoginRequest,
    MeResponse,
    Page,
    PatientActivate,
    PatientCreate,
    PatientListItem,
    TokenResponse,
)

router = APIRouter(tags=["identity"])


@router.get("/me/emergency-contacts")
async def get_my_emergency_contacts(actor: PatientDep, session: SessionDep) -> list[EmergencyContact]:
    assert actor.patient_id is not None
    access = await service.ensure_patient_access(session, actor, actor.patient_id)
    return [EmergencyContact.model_validate(c) for c in access.patient.emergency_contacts]


@router.post("/me/emergency-contacts", status_code=201)
async def post_emergency_contact(
    body: EmergencyContact, actor: PatientDep, session: SessionDep, clock: ClockDep
) -> list[EmergencyContact]:
    contacts = await service.add_emergency_contact(session, actor, body, now=clock.now())
    return [EmergencyContact.model_validate(c) for c in contacts]


@router.get("/patients/{patient_id}/emergency-contacts")
async def get_emergency_contacts(
    patient_id: uuid.UUID, actor: DoctorDep, session: SessionDep
) -> list[EmergencyContact]:
    access = await service.ensure_patient_access(session, actor, patient_id)
    return [EmergencyContact.model_validate(c) for c in access.patient.emergency_contacts]


class TransferRequest(BaseModel):
    new_doctor_id: uuid.UUID


@router.post("/auth/login")
async def post_login(body: LoginRequest, session: SessionDep, settings: SettingsDep, clock: ClockDep) -> TokenResponse:
    """POST /auth/login — LoginRequest → TokenResponse."""
    token, role = await service.login(
        session,
        email=body.email,
        password=body.password,
        secret=settings.jwt_secret.get_secret_value(),
        minutes=settings.access_token_minutes,
        now=clock.now(),
    )
    return TokenResponse(access_token=token, role=role)


@router.get("/auth/me")
async def get_me(actor: ActorDep, session: SessionDep) -> MeResponse:
    """GET /auth/me — MeResponse."""
    if actor.doctor_id is not None:
        doctor = await session.get(Doctor, actor.doctor_id)
        assert doctor is not None
        return MeResponse(user_id=actor.user_id, role=actor.role, full_name=doctor.full_name, doctor_id=doctor.id)
    if actor.patient_id is not None:
        patient = await session.get(Patient, actor.patient_id)
        assert patient is not None
        return MeResponse(
            user_id=actor.user_id,
            role=actor.role,
            full_name=patient.full_name,
            first_name=patient.first_name,
            patient_id=patient.id,
        )
    user = await session.get(User, actor.user_id)  # pending or admin: the name given at registration
    assert user is not None
    return MeResponse(user_id=actor.user_id, role=actor.role, full_name=user.full_name or user.email)


@router.post("/auth/activate", status_code=201)
async def post_activate(
    body: PatientActivate, session: SessionDep, settings: SettingsDep, clock: ClockDep
) -> TokenResponse:
    """POST /auth/activate — PatientActivate → TokenResponse."""
    now = clock.now()
    patient = await service.activate_patient(
        session,
        invite_code=body.invite_code,
        email=body.email,
        password=body.password,
        invite_secret=settings.invite_secret.get_secret_value(),
        now=now,
    )
    assert patient.user_id is not None
    token = issue_token(
        patient.user_id,
        Role.PATIENT,
        secret=settings.jwt_secret.get_secret_value(),
        minutes=settings.access_token_minutes,
        now=now,
    )
    return TokenResponse(access_token=token, role=Role.PATIENT)


@router.post("/patients", status_code=201)
async def post_patient(
    body: PatientCreate, actor: DoctorDep, session: SessionDep, settings: SettingsDep, clock: ClockDep
) -> InviteIssued:
    """POST /patients — PatientCreate → InviteIssued (doctor)."""
    patient, code, expires = await service.create_patient(
        session,
        actor,
        last_name=body.last_name,
        first_name=body.first_name,
        middle_name=body.middle_name,
        birth_date=body.birth_date,
        sex=body.sex,
        timezone=body.timezone,
        invite_secret=settings.invite_secret.get_secret_value(),
        now=clock.now(),
        invite_days=settings.invite_days,
    )
    return InviteIssued(patient_id=patient.id, invite_code=code, expires_at=expires)


@router.post("/patients/{patient_id}/invite")
async def post_invite(
    patient_id: uuid.UUID, actor: DoctorDep, session: SessionDep, settings: SettingsDep, clock: ClockDep
) -> InviteIssued:
    """POST /patients/{id}/invite — re-issue the invite code (treating doctor)."""
    code, expires = await service.reissue_invite(
        session,
        actor,
        patient_id,
        invite_secret=settings.invite_secret.get_secret_value(),
        now=clock.now(),
        invite_days=settings.invite_days,
    )
    return InviteIssued(patient_id=patient_id, invite_code=code, expires_at=expires)


@router.get("/patients")
async def get_patients(
    actor: DoctorDep,
    session: SessionDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Page[PatientListItem]:
    """GET /patients?offset&limit — Page[PatientListItem] (doctor)."""
    rows, total = await service.list_patients(session, actor, offset=offset, limit=limit)
    items = [
        PatientListItem(
            patient_id=p.id,
            full_name=p.full_name,
            last_name=p.last_name,
            first_name=p.first_name,
            middle_name=p.middle_name,
            birth_date=p.birth_date,
            has_account=p.user_id is not None,
        )
        for p in rows
    ]
    return Page(items=items, total=total, offset=offset, limit=limit)


@router.post("/patients/{patient_id}/transfer")
async def post_transfer(
    patient_id: uuid.UUID, body: TransferRequest, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> PatientListItem:
    """POST /patients/{id}/transfer — hand the patient over to another doctor (open alerts follow)."""
    p = await service.transfer_patient(session, actor, patient_id, body.new_doctor_id, now=clock.now())
    return PatientListItem(
        patient_id=p.id,
        full_name=p.full_name,
        last_name=p.last_name,
        first_name=p.first_name,
        middle_name=p.middle_name,
        birth_date=p.birth_date,
        has_account=p.user_id is not None,
    )
