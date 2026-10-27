"""Endpoints: demo mode — «Попробовать как пациент / как врач» (404 unless ``KOMPAS_DEMO_MODE`` is on)."""

from fastapi import APIRouter

from kompas.api.deps import ClockDep, KnowledgeDep, SessionDep, SettingsDep
from kompas.core.config import Settings
from kompas.core.errors import NotFoundError
from kompas.core.security import issue_token
from kompas.modules.demo import service
from kompas.modules.identity.schemas import TokenResponse

router = APIRouter(tags=["demo"])


def _require_demo(settings: Settings) -> None:
    if not settings.demo_mode:
        raise NotFoundError("Демо-режим выключен")


def _token(account: service.DemoAccount, settings: Settings, clock: ClockDep) -> TokenResponse:
    token = issue_token(
        account.user_id,
        account.role,
        secret=settings.jwt_secret.get_secret_value(),
        minutes=settings.access_token_minutes,
        now=clock.now(),
    )
    return TokenResponse(access_token=token, role=account.role)


@router.post("/demo/patient", status_code=201)
async def post_demo_patient(
    session: SessionDep, settings: SettingsDep, knowledge: KnowledgeDep, clock: ClockDep
) -> TokenResponse:
    """POST — the persistent synthetic patient shared with the doctor demo, and a token."""
    _require_demo(settings)
    account = await service.demo_patient(
        session, knowledge, invite_secret=settings.invite_secret.get_secret_value(), now=clock.now()
    )
    return _token(account, settings, clock)


@router.post("/demo/doctor", status_code=201)
async def post_demo_doctor(
    session: SessionDep, settings: SettingsDep, knowledge: KnowledgeDep, clock: ClockDep
) -> TokenResponse:
    """POST — the same demo patient's treating doctor; seed the shared cohort once."""
    _require_demo(settings)
    account = await service.demo_doctor(
        session, knowledge, invite_secret=settings.invite_secret.get_secret_value(), now=clock.now()
    )
    return _token(account, settings, clock)
