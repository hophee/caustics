"""Identity use cases and the access policy.

The policy is the single place that decides who may touch a patient's data (M1, B03): a patient only
their own; a doctor only their patients (``treating_doctor_id``). Missing and foreign objects give the
same NotFoundError, so ids of other doctors' patients can't be probed; PermissionDeniedError (403) is
reserved for a role mismatch on one's own data (e.g. a patient calling a doctor-only action).
Every module resolves child ids (case, result, entry, alert) to a patient and calls
``ensure_patient_access`` before reading or writing.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.core.errors import (
    InvalidInputError,
    InvalidStateError,
    NotFoundError,
    PermissionDeniedError,
    Problem,
    UnauthenticatedError,
)
from kompas.core.security import (
    Role,
    burn_password_check,
    code_hmac,
    floor_to_second,
    hash_password,
    issue_token,
    new_code,
    verify_password,
)
from kompas.modules.audit import service as audit
from kompas.modules.cases.domain.profile import Sex, SuicideRisk, Tristate
from kompas.modules.cases.models import PatientProfileRow
from kompas.modules.identity.models import Doctor, Patient, User
from kompas.modules.identity.schemas import EmergencyContact

INVITE_DAYS = 14
_BAD_LOGIN = "Неверный email или пароль"


async def add_emergency_contact(
    session: AsyncSession, actor: Actor, contact: EmergencyContact, *, now: datetime
) -> list[dict[str, str]]:
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Контакты может добавлять только пациент")
    patient = await session.scalar(
        select(Patient).where(Patient.id == actor.patient_id).with_for_update().execution_options(populate_existing=True)
    )
    if patient is None:
        raise NotFoundError("Пациент не найден")
    if len(patient.emergency_contacts) >= 5:
        raise InvalidStateError("Можно добавить не более 5 экстренных контактов")
    patient.emergency_contacts = [*patient.emergency_contacts, contact.model_dump()]
    await session.flush()
    await audit.record(
        session, actor, "patient.emergency_contact_added",
        entity_type="patient", entity_id=patient.id, patient_id=patient.id, details={}, now=now,
    )
    return patient.emergency_contacts


async def _user_by_email(session: AsyncSession, email: str) -> User | None:
    return await session.scalar(select(User).where(func.lower(User.email) == email.strip().lower()))


async def _new_user(
    session: AsyncSession,
    email: str,
    password: str,
    role: Role,
    now: datetime,
    *,
    full_name: str | None = None,
    demo: bool = False,
) -> User:
    if await _user_by_email(session, email) is not None:
        raise InvalidStateError(
            "Этот email уже зарегистрирован", (Problem("email_taken", "Email уже зарегистрирован", "email"),)
        )
    user = User(
        id=uuid.uuid4(),
        created_at=now,
        email=email.strip(),
        password_hash=hash_password(password),
        password_changed_at=now,
        role=role,
        is_active=True,
        full_name=full_name,
        demo=demo,
    )
    session.add(user)
    await session.flush()
    return user


def _require_doctor(actor: Actor) -> uuid.UUID:
    if not actor.is_doctor or actor.doctor_id is None:
        raise PermissionDeniedError("Действие доступно только врачу")
    return actor.doctor_id


def _invite(secret: str, now: datetime, days: int) -> tuple[str, str, datetime]:
    code = new_code()
    return code, code_hmac(code, secret=secret), now + timedelta(days=days)


@dataclass(frozen=True, slots=True)
class PatientAccess:
    """Result of a successful access check."""

    patient: Patient
    is_self: bool  # the actor is this patient
    is_treating_doctor: bool


async def create_doctor(
    session: AsyncSession,
    *,
    email: str,
    password: str,
    full_name: str,
    specialty: str,
    now: datetime,
    demo: bool = False,
) -> Doctor:
    """Admin/seed operation: user + doctor rows.

    Raises:
        InvalidStateError: email already registered.
    """
    user = await _new_user(session, email, password, Role.DOCTOR, now, full_name=full_name, demo=demo)
    doctor = Doctor(id=uuid.uuid4(), created_at=now, user_id=user.id, full_name=full_name, specialty=specialty)
    session.add(doctor)
    await session.flush()
    return doctor


async def login(
    session: AsyncSession, *, email: str, password: str, secret: str, minutes: int, now: datetime
) -> tuple[str, Role]:
    """Check credentials and issue a token. Runs a dummy argon2 verification for unknown emails so
    response time doesn't reveal which emails exist.

    Raises:
        UnauthenticatedError: unknown email, wrong password or inactive user (same message for all).
    """
    user = await _user_by_email(session, email)
    if user is None:
        burn_password_check(password)
        raise UnauthenticatedError(_BAD_LOGIN)
    if not verify_password(password, user.password_hash) or not user.is_active:
        raise UnauthenticatedError(_BAD_LOGIN)
    return issue_token(user.id, user.role, secret=secret, minutes=minutes, now=now), user.role


async def resolve_actor(session: AsyncSession, user_id: uuid.UUID, issued_at: datetime) -> Actor:
    """Build the Actor for a token. The role comes from the database, not from the token (D-088):
    a role granted by an admin or gained by linking an invite code applies at once, and a revoked
    one stops working at once.

    Raises:
        UnauthenticatedError: user missing/inactive, or token issued before ``password_changed_at``
        (compared after flooring that to whole seconds, like JWT ``iat``).
    """
    user = await session.get(User, user_id)
    if user is None or not user.is_active:
        raise UnauthenticatedError("Недействительный токен")
    if issued_at < floor_to_second(user.password_changed_at):
        raise UnauthenticatedError("Пароль изменён — войдите заново")
    role = user.role
    if role in (Role.PENDING, Role.ADMIN):
        return Actor(user_id=user.id, role=role, doctor_id=None, patient_id=None)
    if role is Role.DOCTOR:
        doctor_id = await session.scalar(select(Doctor.id).where(Doctor.user_id == user.id))
        if doctor_id is None:
            raise UnauthenticatedError("Недействительный токен")
        return Actor(user_id=user.id, role=role, doctor_id=doctor_id, patient_id=None)
    patient_id = await session.scalar(select(Patient.id).where(Patient.user_id == user.id))
    if patient_id is None:
        raise UnauthenticatedError("Недействительный токен")
    return Actor(user_id=user.id, role=role, doctor_id=None, patient_id=patient_id)


async def create_patient(
    session: AsyncSession,
    actor: Actor,
    *,
    last_name: str,
    first_name: str,
    middle_name: str = "",
    birth_date: date,
    sex: Sex,
    timezone: str,
    invite_secret: str,
    now: datetime,
    invite_days: int = INVITE_DAYS,
) -> tuple[Patient, str, datetime]:
    """Doctor creates a patient (treating doctor = actor) **and** the safety profile row (tristates
    UNKNOWN, list facts NULL, medications never reviewed); returns (patient, plain invite code,
    expiry). Audited.

    Raises:
        PermissionDeniedError: actor is not a doctor. InvalidInputError: unknown time zone.
    """
    doctor_id = _require_doctor(actor)
    try:
        ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError):
        raise InvalidInputError(
            f"Неизвестный часовой пояс {timezone!r}",
            (Problem("invalid_input", "Неизвестный часовой пояс", "timezone"),),
        ) from None
    code, digest, expires = _invite(invite_secret, now, invite_days)
    patient = Patient(
        id=uuid.uuid4(),
        created_at=now,
        user_id=None,
        treating_doctor_id=doctor_id,
        last_name=last_name.strip(),
        first_name=first_name.strip(),
        middle_name=middle_name.strip(),
        birth_date=birth_date,
        invite_hmac=digest,
        invite_expires_at=expires,
    )
    session.add(patient)
    await session.flush()
    session.add(
        PatientProfileRow(
            patient_id=patient.id,
            sex=sex,
            timezone=timezone,
            pregnant=Tristate.UNKNOWN,
            lactating=Tristate.UNKNOWN,
            substance_use=Tristate.UNKNOWN,
            suicide_risk=SuicideRisk.UNKNOWN,
            diagnoses=[],
            allergies=None,
            intolerant_excipients=None,
            past_intolerance=None,
            past_inefficacy=None,
            current_medications_reviewed_at=None,
            weight_kg=None,
            updated_at=now,
            updated_by=actor.user_id,
        )
    )
    await audit.record(
        session,
        actor,
        "patient.created",
        entity_type="patient",
        entity_id=patient.id,
        patient_id=patient.id,
        details={"invite_expires_at": expires.isoformat()},
        now=now,
    )
    await session.flush()
    return patient, code, expires


async def reissue_invite(
    session: AsyncSession,
    actor: Actor,
    patient_id: uuid.UUID,
    *,
    invite_secret: str,
    now: datetime,
    invite_days: int = INVITE_DAYS,
) -> tuple[str, datetime]:
    """Treating doctor issues a new code (the old one stops working). Audited.

    Raises:
        InvalidStateError: the patient already has an account.
    """
    _require_doctor(actor)
    access = await ensure_patient_access(session, actor, patient_id)
    patient = access.patient
    if patient.user_id is not None:
        raise InvalidStateError("У пациента уже есть учётная запись")
    code, digest, expires = _invite(invite_secret, now, invite_days)
    patient.invite_hmac = digest
    patient.invite_expires_at = expires
    await audit.record(
        session,
        actor,
        "patient.invite_reissued",
        entity_type="patient",
        entity_id=patient.id,
        patient_id=patient.id,
        details={"invite_expires_at": expires.isoformat()},
        now=now,
    )
    await session.flush()
    return code, expires


async def claim_invite(session: AsyncSession, invite_code: str, *, invite_secret: str, now: datetime) -> Patient:
    """Lock the patient card a valid, unused, unexpired invite code belongs to.

    Raises:
        InvalidInputError: unknown, used or expired code.
    """
    digest = code_hmac(invite_code, secret=invite_secret)
    patient = await session.scalar(
        select(Patient)
        .where(Patient.invite_hmac == digest, Patient.user_id.is_(None), Patient.invite_expires_at > now)
        .with_for_update()
    )
    if patient is None:
        raise InvalidInputError(
            "Код приглашения неверный, уже использован или истёк",
            (Problem("invite_invalid", "Код приглашения недействителен", "invite_code"),),
        )
    return patient


async def activate_patient(
    session: AsyncSession, *, invite_code: str, email: str, password: str, invite_secret: str, now: datetime
) -> Patient:
    """Create the patient's account atomically:
    ``UPDATE patient SET user_id=…, invite_hmac=NULL WHERE invite_hmac=:h AND user_id IS NULL AND
    invite_expires_at > now RETURNING …`` — a race can't activate twice. Audited.

    Raises:
        InvalidInputError: unknown, used or expired code. InvalidStateError: email already registered.
    """
    # The code is checked (and its row locked) before the email, so a caller without a valid code
    # can't learn which emails are registered.
    patient = await claim_invite(session, invite_code, invite_secret=invite_secret, now=now)
    user = await _new_user(session, email, password, Role.PATIENT, now)
    patient.user_id = user.id
    patient.invite_hmac = None
    patient.invite_expires_at = None
    await session.flush()
    actor = Actor(user_id=user.id, role=Role.PATIENT, doctor_id=None, patient_id=patient.id)
    await audit.record(
        session,
        actor,
        "patient.activated",
        entity_type="patient",
        entity_id=patient.id,
        patient_id=patient.id,
        details={},
        now=now,
    )
    return patient


async def transfer_patient(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, new_doctor_id: uuid.UUID, *, now: datetime
) -> Patient:
    """Change the treating doctor (current treating doctor only). In the same audited transaction,
    open (not handled) alerts and their pending retries are re-routed to the new doctor, so the old
    doctor — who loses access — stops being notified.

    Raises:
        NotFoundError: unknown doctor (or no access to the patient).
    """
    from kompas.modules.monitoring import service as monitoring  # cycle: monitoring routes via identity

    _require_doctor(actor)
    await ensure_patient_access(session, actor, patient_id)
    # Lock after the access check; waits for transactions routing alerts via ``treating_doctor``.
    patient = await session.get(Patient, patient_id, with_for_update=True, populate_existing=True)
    if patient is None or patient.treating_doctor_id != actor.doctor_id:
        raise NotFoundError("Пациент не найден")
    if await session.get(Doctor, new_doctor_id) is None:
        raise NotFoundError("Врач не найден")
    old_doctor_id = patient.treating_doctor_id
    if new_doctor_id == old_doctor_id:
        return patient
    patient.treating_doctor_id = new_doctor_id
    await session.flush()
    moved = await monitoring.reroute_open_alerts(session, patient.id, new_doctor_id, now=now)
    await audit.record(
        session,
        actor,
        "patient.transferred",
        entity_type="patient",
        entity_id=patient.id,
        patient_id=patient.id,
        details={"from_doctor": str(old_doctor_id), "to_doctor": str(new_doctor_id), "alerts_rerouted": moved},
        now=now,
    )
    return patient


async def list_patients(session: AsyncSession, actor: Actor, *, offset: int, limit: int) -> tuple[list[Patient], int]:
    """The doctor's patients (page, total).

    Raises:
        PermissionDeniedError: actor is not a doctor.
    """
    doctor_id = _require_doctor(actor)
    where = Patient.treating_doctor_id == doctor_id
    total = await session.scalar(select(func.count()).select_from(Patient).where(where))
    rows = await session.scalars(
        select(Patient).where(where).order_by(Patient.full_name, Patient.id).offset(offset).limit(limit)
    )
    return list(rows.all()), total or 0


async def ensure_patient_access(session: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> PatientAccess:
    """Return the access context, or raise NotFoundError for missing **and** foreign patients."""
    patient = await session.get(Patient, patient_id)
    if patient is not None:
        if actor.is_patient and actor.patient_id == patient.id:
            return PatientAccess(patient, is_self=True, is_treating_doctor=False)
        if actor.is_doctor and actor.doctor_id == patient.treating_doctor_id:
            return PatientAccess(patient, is_self=False, is_treating_doctor=True)
    raise NotFoundError("Пациент не найден")


async def treating_doctor(session: AsyncSession, patient_id: uuid.UUID) -> Doctor:
    """The patient's treating doctor (always exists: NOT NULL), used to route alerts. Takes a share
    lock on the patient row, so a concurrent transfer (which locks it for update) either finishes
    first or waits until this transaction — and the alert it routes — is committed and re-routable."""
    doctor_id = await session.scalar(
        select(Patient.treating_doctor_id).where(Patient.id == patient_id).with_for_update(read=True)
    )
    doctor = await session.get(Doctor, doctor_id) if doctor_id else None
    if doctor is None:
        raise NotFoundError("Пациент не найден")
    return doctor
