"""The built website is served from the API's origin (D-114)."""

from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from kompas.api.app import create_app
from kompas.core.clock import FixedClock
from kompas.core.config import Settings
from kompas.modules.knowledge.bundle import Knowledge
from tests.api.conftest import NOW


@pytest.fixture
async def site(settings: Settings, knowledge: Knowledge, tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    web = tmp_path / "dist"
    (web / "assets").mkdir(parents=True)
    (web / "index.html").write_text("<!doctype html><title>Клинический компас</title>", encoding="utf-8")
    (web / "favicon.svg").write_text("<svg/>", encoding="utf-8")
    (web / "assets" / "app-1a2b.js").write_text("console.log(1)", encoding="utf-8")
    (tmp_path / "secret.txt").write_text("outside", encoding="utf-8")
    app = create_app(settings.model_copy(update={"web_dir": web}), clock=FixedClock(NOW), knowledge=knowledge)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://kompas.test") as client,
    ):
        yield client


async def test_pages_fall_back_to_index(site: httpx.AsyncClient) -> None:
    for path in ("/", "/login", "/d/patients/123/therapy"):
        r = await site.get(path)
        assert r.status_code == 200 and "Клинический компас" in r.text, path
        assert r.headers["cache-control"] == "no-cache"


async def test_files_and_assets(site: httpx.AsyncClient) -> None:
    r = await site.get("/assets/app-1a2b.js")
    assert r.status_code == 200 and "immutable" in r.headers["cache-control"]
    assert (await site.get("/favicon.svg")).headers["cache-control"] == "no-cache"
    assert (await site.get("/assets/missing.js")).status_code == 404


async def test_api_and_traversal_are_not_the_website(site: httpx.AsyncClient) -> None:
    r = await site.get("/api/v1/does-not-exist")
    assert r.status_code == 404 and r.json()["error"]["code"] == "not_found"
    assert (await site.get("/api/v1/auth/me")).status_code == 401  # real routes still win
    r = await site.get("/%2E%2E/secret.txt")
    assert r.status_code in (200, 404) and "outside" not in r.text


async def test_no_build_means_api_only(settings: Settings, knowledge: Knowledge, tmp_path: Path) -> None:
    app = create_app(settings.model_copy(update={"web_dir": tmp_path / "none"}), knowledge=knowledge)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://kompas.test") as client,
    ):
        assert (await client.get("/")).status_code == 404
