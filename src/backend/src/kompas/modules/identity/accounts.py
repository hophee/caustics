"""Accounts (D-088): anyone registers and starts as PENDING — no clinical access. A pending user becomes
a patient by entering the invite code of a card their doctor created, or a doctor when an admin grants
the role. Admins are created from the CLI only. Every role change is audited (``actor=None`` = CLI).
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import ColumnElement, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.core.errors import InvalidStateError, NotFoundError, PermissionDeniedError
from kompas.core.security import Role
from kompas.modules.audit import service as audit
from kompas.modules.identity.models import Doctor, Patient, User
from kompas.modules.identity.service import _new_user, _user_by_email, claim_invite

DEFAULT_SPECIALTY = "психиатр"


async def register(session: AsyncSession, *, email: str, password: str, full_name: str, now: datetime) -> User:
    """A person creates an account (role PENDING).

    Raises:
        InvalidStateError: email already registered.
    """
    user = await _new_user(session, email, password, Role.PENDING, now, full_name=full_name.strip())
    actor = Actor(user_id=user.id, role=Role.PENDING, doctor_id=None, patient_id=None)
    await audit.record(
        session, actor, "user.registered", entity_type="user", entity_id=user.id, patient_id=None, details={}, now=now
    )
    return user


async def link_invite(
    session: AsyncSession, actor: Actor, *, invite_code: str, invite_secret: str, now: datetime
) -> Patient:
    """A pending user enters a doctor's invite code and becomes that card's patient (atomic: the card is
    locked; a used or expired code is refused). The card keeps the name the doctor entered.

    Raises:
        InvalidStateError: the account already has a role. InvalidInputError: bad code.
    """
    if actor.role is not Role.PENDING:
        raise InvalidStateError("Код врача вводится только в новом аккаунте без роли")
    patient = await claim_invite(session, invite_code, invite_secret=invite_secret, now=now)
    user = await session.get(User, actor.user_id, with_for_update=True)
    assert user is not None
    if user.role is not Role.PENDING:  # changed concurrently
        raise InvalidStateError("Код врача вводится только в новом аккаунте без роли")
    patient.user_id, patient.invite_hmac, patient.invite_expires_at = user.id, None, None
    user.role = Role.PATIENT
    await session.flush()
    await audit.record(
        session,
        Actor(user_id=user.id, role=Role.PATIENT, doctor_id=None, patient_id=patient.id),
        "patient.activated",
        entity_type="patient",
        entity_id=patient.id,
        patient_id=patient.id,
        details={"via": "registered_account"},
        now=now,
    )
    return patient


def _admin(actor: Actor | None) -> None:
    if actor is not None and not actor.is_admin:
        raise PermissionDeniedError("Действие доступно только администратору")


async def _target(session: AsyncSession, user_id: uuid.UUID) -> User:
    user = await session.get(User, user_id, with_for_update=True)
    if user is None:
        raise NotFoundError("Пользователь не найден")
    return user


async def grant_doctor(
    session: AsyncSession,
    actor: Actor | None,
    user_id: uuid.UUID,
    *,
    specialty: str | None,
    now: datetime,
) -> Doctor:
    """Make a pending user a doctor (``actor`` None = CLI). A previously revoked doctor gets the same
    doctor record back. Granting to a doctor again is a no-op.

    Raises:
        PermissionDeniedError: the caller is not an admin.
        InvalidStateError: the user is a patient or an admin.
    """
    _admin(actor)
    user = await _target(session, user_id)
    doctor = await session.scalar(select(Doctor).where(Doctor.user_id == user.id))
    if user.role is Role.DOCTOR and doctor is not None:
        return doctor
    if user.role is not Role.PENDING:
        raise InvalidStateError("Роль врача можно дать только аккаунту без роли (не пациенту и не администратору)")
    if doctor is None:
        doctor = Doctor(
            id=uuid.uuid4(),
            created_at=now,
            user_id=user.id,
            full_name=user.full_name or user.email,
            specialty=(specialty or "").strip() or DEFAULT_SPECIALTY,
        )
        session.add(doctor)
    elif specialty and specialty.strip():
        doctor.specialty = specialty.strip()
    user.role = Role.DOCTOR
    await session.flush()
    await audit.record(
        session,
        actor,
        "user.doctor_granted",
        entity_type="user",
        entity_id=user.id,
        patient_id=None,
        details={"doctor_id": str(doctor.id), "via": "cli" if actor is None else "admin"},
        now=now,
    )
    return doctor


async def revoke_doctor(session: AsyncSession, actor: Actor | None, user_id: uuid.UUID, *, now: datetime) -> User:
    """Take the doctor role away (the account becomes pending; the doctor record and its history stay).

    Raises:
        PermissionDeniedError: the caller is not an admin.
        InvalidStateError: not a doctor, or the doctor still treats patients (transfer them first).
    """
    _admin(actor)
    user = await _target(session, user_id)
    if user.role is not Role.DOCTOR:
        raise InvalidStateError("Этот пользователь не врач")
    doctor_id = await session.scalar(select(Doctor.id).where(Doctor.user_id == user.id))
    patients = await session.scalar(
        select(func.count()).select_from(Patient).where(Patient.treating_doctor_id == doctor_id)
    )
    if patients:
        raise InvalidStateError(f"У врача {patients} пациент(ов) — сначала передайте их другому врачу")
    user.role = Role.PENDING
    await audit.record(
        session,
        actor,
        "user.doctor_revoked",
        entity_type="user",
        entity_id=user.id,
        patient_id=None,
        details={"via": "cli" if actor is None else "admin"},
        now=now,
    )
    return user


async def create_admin(session: AsyncSession, *, email: str, password: str, full_name: str, now: datetime) -> User:
    """CLI only: an admin account (manages roles; no clinical access).

    Raises:
        InvalidStateError: email already registered.
    """
    user = await _new_user(session, email, password, Role.ADMIN, now, full_name=full_name)
    await audit.record(
        session,
        None,
        "user.admin_created",
        entity_type="user",
        entity_id=user.id,
        patient_id=None,
        details={"via": "cli"},
        now=now,
    )
    return user


async def user_by_email(session: AsyncSession, email: str) -> User:
    """Raises NotFoundError for an unknown email (CLI helper)."""
    user = await _user_by_email(session, email)
    if user is None:
        raise NotFoundError(f"Пользователь {email} не найден")
    return user


@dataclass(frozen=True, slots=True)
class UserRow:
    user: User
    name: str  # the doctor's / patient's card name, else the registration name
    patients: int | None  # doctors: how many patients they treat
    doctor_name: str | None  # patients: their treating doctor


async def list_users(
    session: AsyncSession,
    actor: Actor,
    *,
    query: str | None,
    role: Role | None,
    offset: int,
    limit: int,
) -> tuple[list[UserRow], int]:
    """Accounts for the admin page, newest first; ``query`` matches email or name (case-insensitive)."""
    if not actor.is_admin:
        raise PermissionDeniedError("Действие доступно только администратору")
    doctor_name = select(Doctor.full_name).where(Doctor.user_id == User.id).scalar_subquery()
    patient_name = select(Patient.full_name).where(Patient.user_id == User.id).scalar_subquery()
    name = func.coalesce(doctor_name, patient_name, User.full_name, User.email)
    where: list[ColumnElement[bool]] = [User.demo.is_(False)]
    if role is not None:
        where.append(User.role == role)
    if query and query.strip():
        like = f"%{query.strip().lower()}%"
        where.append(or_(func.lower(User.email).like(like), func.lower(name).like(like)))
    total = await session.scalar(select(func.count()).select_from(User).where(*where))
    rows = (
        await session.execute(
            select(User, name).where(*where).order_by(User.created_at.desc(), User.id).offset(offset).limit(limit)
        )
    ).all()
    users = [u for u, _ in rows]
    counts = dict(
        (
            await session.execute(
                select(Doctor.user_id, func.count(Patient.id))
                .join(Patient, Patient.treating_doctor_id == Doctor.id)
                .where(Doctor.user_id.in_([u.id for u in users]))
                .group_by(Doctor.user_id)
            )
        ).all()
    )
    doctors = dict(
        (
            await session.execute(
                select(Patient.user_id, Doctor.full_name)
                .join(Doctor, Doctor.id == Patient.treating_doctor_id)
                .where(Patient.user_id.in_([u.id for u in users]))
            )
        ).all()
    )
    out = [
        UserRow(
            u,
            str(n),
            counts.get(u.id, 0) if u.role is Role.DOCTOR else None,
            doctors.get(u.id) if u.role is Role.PATIENT else None,
        )
        for u, n in rows
    ]
    return out, total or 0
