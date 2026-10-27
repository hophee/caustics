"""Fixtures for API tests against a real PostgreSQL.

The database comes from ``KOMPAS_TEST_DATABASE_URL`` or, if unset, from the pip-installed dev server
(``uv run --project tools/devdb kompas-devdb start --database kompas_test``). Migrations run once per
session; every test starts with empty tables (TRUNCATE after each test). The clock is a FixedClock
the test can move.
"""

import os
import subprocess
import sys
import uuid
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from kompas.api.app import create_app
from kompas.core.clock import FixedClock
from kompas.core.config import Settings
from kompas.core.db import transaction
from kompas.models_all import Base
from kompas.modules.identity.service import create_doctor
from kompas.modules.knowledge.bundle import Knowledge
from kompas.worker import handlers, tick

BACKEND = Path(__file__).resolve().parents[2]
REPO = BACKEND.parents[1]
NOW = datetime(2026, 10, 19, 9, 0, tzinfo=UTC)
PASSWORD = "correct-horse-battery"


@pytest.fixture(scope="session")
def database_url() -> str:
    url = os.environ.get("KOMPAS_TEST_DATABASE_URL")
    if not url:
        try:
            out = subprocess.run(
                [
                    "uv",
                    "run",
                    "--project",
                    str(REPO / "tools/devdb"),
                    "kompas-devdb",
                    "start",
                    "--database",
                    "kompas_test",
                ],
                capture_output=True,
                text=True,
                check=True,
                timeout=300,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            pytest.skip(f"no test database (set KOMPAS_TEST_DATABASE_URL): {exc}")
        url = out.stdout.strip().splitlines()[-1]
    env = {**os.environ, "KOMPAS_DATABASE_URL": url}
    alembic = [sys.executable, "-m", "alembic"]
    subprocess.run([*alembic, "downgrade", "base"], cwd=BACKEND, env=env, check=True, capture_output=True)
    subprocess.run([*alembic, "upgrade", "head"], cwd=BACKEND, env=env, check=True, capture_output=True)
    return url


@pytest.fixture(scope="session")
def settings(database_url: str) -> Settings:
    return Settings(
        database_url=database_url,
        jwt_secret=SecretStr("test-jwt-secret-" + "x" * 32),
        invite_secret=SecretStr("test-invite-secret-" + "y" * 32),
    )


@dataclass
class Env:
    """Everything a test needs: HTTP client, clock, session factory, helpers."""

    client: httpx.AsyncClient
    clock: FixedClock
    factory: async_sessionmaker[AsyncSession]
    settings: Settings
    knowledge: Knowledge

    async def doctor(self, email: str, name: str = "Доктор Тест") -> dict[str, str]:
        """Create a doctor and return auth headers."""
        async with transaction(self.factory) as session:
            await create_doctor(
                session, email=email, password=PASSWORD, full_name=name, specialty="психиатр", now=self.clock.now()
            )
        return await self.login(email)

    async def login(self, email: str, password: str = PASSWORD) -> dict[str, str]:
        r = await self.client.post("/api/v1/auth/login", json={"email": email, "password": password})
        assert r.status_code == 200, r.text
        return {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def patient(self, doctor: dict[str, str], email: str, **fields: Any) -> tuple[str, dict[str, str]]:
        """Doctor creates a patient, patient activates; returns (patient_id, patient headers)."""
        body = {
            "last_name": "Тестовая",
            "first_name": "Анна",
            "middle_name": "",
            "birth_date": "1992-03-01",
            "sex": "female",
            **fields,
        }
        r = await self.client.post("/api/v1/patients", json=body, headers=doctor)
        assert r.status_code == 201, r.text
        issued = r.json()
        r = await self.client.post(
            "/api/v1/auth/activate", json={"invite_code": issued["invite_code"], "email": email, "password": PASSWORD}
        )
        assert r.status_code == 201, r.text
        return issued["patient_id"], {"Authorization": f"Bearer {r.json()['access_token']}"}

    async def case(self, doctor: dict[str, str], patient_id: str, icd: str = "F33.2") -> str:
        r = await self.client.post(f"/api/v1/patients/{patient_id}/cases", headers=doctor)
        assert r.status_code == 201, r.text
        case_id = r.json()["case_id"]
        r = await self.client.post(f"/api/v1/cases/{case_id}/diagnoses", json={"icd_code": icd}, headers=doctor)
        assert r.status_code == 201, r.text
        return str(case_id)

    async def run_worker(self) -> int:
        """Process the outbox until it is empty (handlers run in-process)."""
        total = 0
        table = handlers(self.knowledge, self.clock)
        while processed := await tick(self.factory, table, self.settings, self.clock):
            total += processed
        return total

    async def sql(self, query: str, **params: Any) -> list[Any]:
        async with self.factory() as session:
            return list((await session.execute(text(query), params)).all())


@pytest.fixture
async def env(settings: Settings, knowledge: Knowledge) -> AsyncIterator[Env]:
    clock = FixedClock(NOW)
    app = create_app(settings, clock=clock, knowledge=knowledge)
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://kompas.test") as client:
            yield Env(client, clock, app.state.session_factory, settings, knowledge)
        async with app.state.engine.begin() as connection:
            tables = ", ".join(t.name for t in Base.metadata.sorted_tables)
            await connection.execute(text(f"TRUNCATE {tables} CASCADE"))


def new_id() -> str:
    return str(uuid.uuid4())


def walk(value: Any) -> Iterator[tuple[str, Any]]:
    """All (key, value) pairs in a JSON document, recursively."""
    if isinstance(value, dict):
        for k, v in value.items():
            yield k, v
            yield from walk(v)
    elif isinstance(value, list):
        for v in value:
            yield from walk(v)


TODAY = date(2026, 10, 19)
