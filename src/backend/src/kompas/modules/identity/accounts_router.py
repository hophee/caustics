"""Endpoints (D-088): self-registration, linking a doctor's invite code, the admin «Пользователи» page."""

import uuid
from datetime import datetime

from fastapi import APIRouter, Query
from pydantic import BaseModel, EmailStr, Field

from kompas.api.deps import ActorDep, AdminDep, ClockDep, SessionDep, SettingsDep
from kompas.core.config import Settings
from kompas.core.security import Role, issue_token
from kompas.modules.identity import accounts
from kompas.modules.identity.schemas import Page, TokenResponse

router = APIRouter(tags=["accounts"])


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=10)
    full_name: str = Field(min_length=1, max_length=200)


class LinkRequest(BaseModel):
    invite_code: str = Field(min_length=1, max_length=64)


class GrantDoctorRequest(BaseModel):
    specialty: str | None = Field(default=None, max_length=100)


class AuthConfig(BaseModel):
    registration: bool  # /auth/register is open
    demo_mode: bool  # /demo/patient and /demo/doctor are available


class UserOut(BaseModel):
    user_id: uuid.UUID
    email: str
    name: str
    role: Role
    is_active: bool
    created_at: datetime
    patients: int | None  # doctors: number of their patients
    doctor_name: str | None  # patients: treating doctor

    @classmethod
    def of(cls, row: accounts.UserRow) -> "UserOut":
        u = row.user
        return cls(
            user_id=u.id,
            email=u.email,
            name=row.name,
            role=u.role,
            is_active=u.is_active,
            created_at=u.created_at,
            patients=row.patients,
            doctor_name=row.doctor_name,
        )


def _token(user_id: uuid.UUID, role: Role, settings: Settings, now: datetime) -> TokenResponse:
    token = issue_token(
        user_id, role, secret=settings.jwt_secret.get_secret_value(), minutes=settings.access_token_minutes, now=now
    )
    return TokenResponse(access_token=token, role=role)


@router.get("/auth/config")
async def get_config(settings: SettingsDep) -> AuthConfig:
    """GET /auth/config — what the login page offers (public)."""
    return AuthConfig(registration=True, demo_mode=settings.demo_mode)


@router.post("/auth/register", status_code=201)
async def post_register(
    body: RegisterRequest, session: SessionDep, settings: SettingsDep, clock: ClockDep
) -> TokenResponse:
    """POST /auth/register — create an account without a role (signed in right away)."""
    now = clock.now()
    user = await accounts.register(session, email=body.email, password=body.password, full_name=body.full_name, now=now)
    return _token(user.id, Role.PENDING, settings, now)


@router.post("/auth/link")
async def post_link(
    body: LinkRequest, actor: ActorDep, session: SessionDep, settings: SettingsDep, clock: ClockDep
) -> TokenResponse:
    """POST /auth/link — a pending account enters a doctor's invite code and becomes that patient."""
    now = clock.now()
    await accounts.link_invite(
        session, actor, invite_code=body.invite_code, invite_secret=settings.invite_secret.get_secret_value(), now=now
    )
    return _token(actor.user_id, Role.PATIENT, settings, now)


@router.get("/admin/users")
async def get_users(
    actor: AdminDep,
    session: SessionDep,
    q: str | None = Query(None, max_length=200),
    role: Role | None = None,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> Page[UserOut]:
    """GET — accounts (demo accounts excluded), newest first; filter by text and role."""
    rows, total = await accounts.list_users(session, actor, query=q, role=role, offset=offset, limit=limit)
    return Page(items=[UserOut.of(r) for r in rows], total=total, offset=offset, limit=limit)


@router.post("/admin/users/{user_id}/doctor")
async def post_grant_doctor(
    user_id: uuid.UUID, body: GrantDoctorRequest, actor: AdminDep, session: SessionDep, clock: ClockDep
) -> dict[str, str]:
    """POST — make an account without a role a doctor."""
    doctor = await accounts.grant_doctor(session, actor, user_id, specialty=body.specialty, now=clock.now())
    return {"user_id": str(user_id), "doctor_id": str(doctor.id), "role": Role.DOCTOR.value}


@router.post("/admin/users/{user_id}/revoke-doctor")
async def post_revoke_doctor(
    user_id: uuid.UUID, actor: AdminDep, session: SessionDep, clock: ClockDep
) -> dict[str, str]:
    """POST — take the doctor role away (only from a doctor without patients)."""
    await accounts.revoke_doctor(session, actor, user_id, now=clock.now())
    return {"user_id": str(user_id), "role": Role.PENDING.value}
