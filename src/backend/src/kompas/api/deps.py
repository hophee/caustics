"""Shared FastAPI dependencies: settings, knowledge, database session, current user.

Routers use the ``Annotated`` aliases (``SessionDep``, ``DoctorDep`` …) so every handler states what
it needs in its signature.
"""

from collections.abc import AsyncIterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kompas.core.actor import Actor
from kompas.core.clock import Clock
from kompas.core.config import Settings
from kompas.core.errors import PermissionDeniedError, UnauthenticatedError
from kompas.core.security import read_token
from kompas.modules.identity.service import resolve_actor
from kompas.modules.knowledge.bundle import Knowledge


def get_settings(request: Request) -> Settings:
    """Settings stored on ``app.state`` at startup (the only settings accessor in request code)."""
    settings: Settings = request.app.state.settings
    return settings


def get_knowledge(request: Request) -> Knowledge:
    """The knowledge bundle loaded at startup."""
    knowledge: Knowledge = request.app.state.knowledge
    return knowledge


def get_clock(request: Request) -> Clock:
    """System clock in production; tests override with a FixedClock."""
    clock: Clock = request.app.state.clock
    return clock


def get_session_factory(request: Request) -> async_sessionmaker[AsyncSession]:
    """The engine's session factory (for code that manages its own short sessions, e.g. SSE)."""
    factory: async_sessionmaker[AsyncSession] = request.app.state.session_factory
    return factory


async def get_session(request: Request) -> AsyncIterator[AsyncSession]:
    """One transaction per request. Registered with ``Depends(get_session, scope="function")`` so the
    commit happens **before** the response is sent: a commit-time failure (constraint, serialization)
    becomes an error response, never a 200. Rolls back if the handler raises."""
    async with get_session_factory(request)() as session, session.begin():
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session, scope="function")]
SettingsDep = Annotated[Settings, Depends(get_settings)]
KnowledgeDep = Annotated[Knowledge, Depends(get_knowledge)]
ClockDep = Annotated[Clock, Depends(get_clock)]


async def get_actor(request: Request, session: SessionDep) -> Actor:
    """Read the bearer token (``Authorization`` header only — never a query parameter) and resolve
    the user.

    Raises:
        UnauthenticatedError: no/invalid token or the user is inactive.
    """
    header = request.headers.get("authorization", "")
    scheme, _, token = header.partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise UnauthenticatedError("Нужна авторизация")
    settings = get_settings(request)
    claims = read_token(token.strip(), secret=settings.jwt_secret.get_secret_value(), now=get_clock(request).now())
    return await resolve_actor(session, claims.user_id, claims.issued_at)


ActorDep = Annotated[Actor, Depends(get_actor)]


def require_doctor(actor: ActorDep) -> Actor:
    """Raises PermissionDeniedError unless the actor is a doctor."""
    if not actor.is_doctor:
        raise PermissionDeniedError("Действие доступно только врачу")
    return actor


def require_patient(actor: ActorDep) -> Actor:
    """Raises PermissionDeniedError unless the actor is a patient."""
    if not actor.is_patient:
        raise PermissionDeniedError("Действие доступно только пациенту")
    return actor


def require_admin(actor: ActorDep) -> Actor:
    """Raises PermissionDeniedError unless the actor is an admin."""
    if not actor.is_admin:
        raise PermissionDeniedError("Действие доступно только администратору")
    return actor


DoctorDep = Annotated[Actor, Depends(require_doctor)]
PatientDep = Annotated[Actor, Depends(require_patient)]
AdminDep = Annotated[Actor, Depends(require_admin)]
