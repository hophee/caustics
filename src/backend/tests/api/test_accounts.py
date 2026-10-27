"""Accounts (D-088): self-registration → pending; invite code → patient; admin grants/revokes doctor;
demo mode creates throwaway synthetic accounts."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx

from kompas.api.app import create_app
from kompas.core.db import transaction
from kompas.modules.identity import accounts
from tests.api.conftest import PASSWORD, Env, new_id


async def register(env: Env, email: str, name: str = "Ирина Новикова") -> dict[str, str]:
    r = await env.client.post("/api/v1/auth/register", json={"email": email, "password": PASSWORD, "full_name": name})
    assert r.status_code == 201, r.text
    assert r.json()["role"] == "pending"
    return {"Authorization": f"Bearer {r.json()['access_token']}"}


async def make_admin(env: Env) -> dict[str, str]:
    async with transaction(env.factory) as session:
        await accounts.create_admin(
            session, email="admin@kompas-demo.ru", password=PASSWORD, full_name="Админ", now=env.clock.now()
        )
    return await env.login("admin@kompas-demo.ru")


async def me(env: Env, headers: dict[str, str]) -> dict[str, object]:
    r = await env.client.get("/api/v1/auth/me", headers=headers)
    assert r.status_code == 200, r.text
    return dict(r.json())


async def test_register_is_pending_with_no_clinical_access(env: Env) -> None:
    person = await register(env, "person@kompas-demo.ru")
    info = await me(env, person)
    assert info["role"] == "pending" and info["full_name"] == "Ирина Новикова"
    assert info["patient_id"] is None and info["doctor_id"] is None
    for method, path in [("GET", "/api/v1/me/diary"), ("GET", "/api/v1/patients"), ("GET", "/api/v1/admin/users")]:
        assert (await env.client.request(method, path, headers=person)).status_code == 403, path
    again = await env.client.post(
        "/api/v1/auth/register", json={"email": "PERSON@kompas-demo.ru", "password": PASSWORD, "full_name": "x"}
    )
    assert again.status_code == 409  # emails are unique case-insensitively
    short = await env.client.post(
        "/api/v1/auth/register", json={"email": "p2@kompas-demo.ru", "password": "short", "full_name": "x"}
    )
    assert short.status_code == 422
    login = await env.client.post("/api/v1/auth/login", json={"email": "person@kompas-demo.ru", "password": PASSWORD})
    assert login.json()["role"] == "pending"


async def test_invite_code_links_a_registered_account(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    card = await env.client.post(
        "/api/v1/patients",
        json={
            "last_name": "Тестовая",
            "first_name": "Анна",
            "middle_name": "",
            "birth_date": "1992-03-01",
            "sex": "female",
        },
        headers=doctor,
    )
    code = card.json()["invite_code"]
    person = await register(env, "anna@kompas-demo.ru", "Анна Т.")
    bad = await env.client.post("/api/v1/auth/link", json={"invite_code": "WRONG-CODE"}, headers=person)
    assert bad.status_code == 422
    linked = await env.client.post("/api/v1/auth/link", json={"invite_code": code}, headers=person)
    assert linked.status_code == 200, linked.text
    # the old token works: the role is read from the database (D-088)
    info = await me(env, person)
    assert info["role"] == "patient" and info["patient_id"] == card.json()["patient_id"]
    assert info["full_name"] == "Тестовая Анна"  # the card keeps the doctor's name
    assert (await env.client.get("/api/v1/me/diary", headers=person)).status_code == 200
    twice = await env.client.post("/api/v1/auth/link", json={"invite_code": code}, headers=person)
    assert twice.status_code == 409  # not a pending account any more
    other = await register(env, "other@kompas-demo.ru")
    used = await env.client.post("/api/v1/auth/link", json={"invite_code": code}, headers=other)
    assert used.status_code == 422
    patients = (await env.client.get("/api/v1/patients", headers=doctor)).json()["items"]
    assert patients[0]["has_account"] is True
    actions = {a for (a,) in await env.sql("SELECT action FROM audit_entry")}
    assert {"user.registered", "patient.activated"} <= actions


async def test_admin_grants_and_revokes_the_doctor_role(env: Env) -> None:
    admin = await make_admin(env)
    person = await register(env, "future-doc@kompas-demo.ru", "Пётр Врачев")
    users = (await env.client.get("/api/v1/admin/users?role=pending", headers=admin)).json()
    target = next(u for u in users["items"] if u["email"] == "future-doc@kompas-demo.ru")
    assert target["name"] == "Пётр Врачев"
    # only an admin
    assert (
        await env.client.post(f"/api/v1/admin/users/{target['user_id']}/doctor", json={}, headers=person)
    ).status_code == 403
    granted = await env.client.post(
        f"/api/v1/admin/users/{target['user_id']}/doctor", json={"specialty": "психотерапевт"}, headers=admin
    )
    assert granted.status_code == 200, granted.text
    doctor_id = granted.json()["doctor_id"]
    info = await me(env, person)  # same token, now a doctor
    assert info["role"] == "doctor" and info["doctor_id"] == doctor_id and info["full_name"] == "Пётр Врачев"
    card = await env.client.post(
        "/api/v1/patients",
        json={
            "last_name": "Пациент",
            "first_name": "Один",
            "middle_name": "",
            "birth_date": "1990-01-01",
            "sex": "male",
        },
        headers=person,
    )
    assert card.status_code == 201
    listed = (await env.client.get("/api/v1/admin/users?q=врачев", headers=admin)).json()["items"]
    assert listed[0]["role"] == "doctor" and listed[0]["patients"] == 1
    # a doctor with patients keeps the role until they are transferred
    busy = await env.client.post(f"/api/v1/admin/users/{target['user_id']}/revoke-doctor", headers=admin)
    assert busy.status_code == 409

    second = await register(env, "doc2@kompas-demo.ru", "Второй Врач")
    second_id = (await me(env, second))["user_id"]
    await env.client.post(f"/api/v1/admin/users/{second_id}/doctor", json={}, headers=admin)
    first_doctor = (await me(env, second))["doctor_id"]
    revoked = await env.client.post(f"/api/v1/admin/users/{second_id}/revoke-doctor", headers=admin)
    assert revoked.status_code == 200
    assert (await me(env, second))["role"] == "pending"
    assert (await env.client.get("/api/v1/patients", headers=second)).status_code == 403  # takes effect at once
    again = await env.client.post(f"/api/v1/admin/users/{second_id}/doctor", json={}, headers=admin)
    assert again.json()["doctor_id"] == first_doctor  # the same doctor record comes back

    # a patient can't be made a doctor
    patient_id, patient = await env.patient(person, "pt@kompas-demo.ru")
    patient_user = (await me(env, patient))["user_id"]
    refused = await env.client.post(f"/api/v1/admin/users/{patient_user}/doctor", json={}, headers=admin)
    assert refused.status_code == 409 and patient_id
    actions = {a for (a,) in await env.sql("SELECT action FROM audit_entry")}
    assert {"user.admin_created", "user.doctor_granted", "user.doctor_revoked"} <= actions


async def test_cli_style_grant_without_actor(env: Env) -> None:
    person = await register(env, "cli-doc@kompas-demo.ru")
    async with transaction(env.factory) as session:
        user = await accounts.user_by_email(session, "CLI-doc@kompas-demo.ru")
        await accounts.grant_doctor(session, None, user.id, specialty=None, now=env.clock.now())
    info = await me(env, person)
    assert info["role"] == "doctor"
    via = await env.sql("SELECT details->>'via' FROM audit_entry WHERE action = 'user.doctor_granted'")
    assert via == [("cli",)]


@asynccontextmanager
async def demo_client(env: Env) -> AsyncIterator[httpx.AsyncClient]:
    app = create_app(env.settings.model_copy(update={"demo_mode": True}), clock=env.clock, knowledge=env.knowledge)
    transport = httpx.ASGITransport(app=app)
    async with (
        app.router.lifespan_context(app),
        httpx.AsyncClient(transport=transport, base_url="http://kompas.test") as c,
    ):
        yield c


async def test_demo_mode(env: Env) -> None:
    assert (await env.client.get("/api/v1/auth/config")).json() == {"registration": True, "demo_mode": False}
    assert (await env.client.post("/api/v1/demo/patient")).status_code == 404
    async with demo_client(env) as client:
        assert (await client.get("/api/v1/auth/config")).json()["demo_mode"] is True
        r = await client.post("/api/v1/demo/patient")
        assert r.status_code == 201, r.text
        patient = {"Authorization": f"Bearer {r.json()['access_token']}"}
        original = (await client.get("/api/v1/auth/me", headers=patient)).json()
        assert original["role"] == "patient"
        diary = (await client.get("/api/v1/me/diary", headers=patient)).json()
        assert len(diary) >= 6
        # The compact test catalogue may omit the demo drug; no prescription is invented then.
        has_demo_drug = bool(env.knowledge.drugs and env.knowledge.drugs.by_substance("маритупирдин"))
        assert len((await client.get("/api/v1/me/prescription", headers=patient)).json()) == int(has_demo_drug)
        assert len((await client.get("/api/v1/me/appointments?scope=upcoming", headers=patient)).json()) == 1
        second = await client.post("/api/v1/demo/patient")
        assert second.status_code == 201
        again = {"Authorization": f"Bearer {second.json()['access_token']}"}
        assert (await client.get("/api/v1/auth/me", headers=again)).json()["user_id"] == original["user_id"]
        signal = await client.post("/api/v1/me/reaction-signals", json={"submission_id": new_id()}, headers=patient)
        assert signal.status_code == 201, signal.text
        event_id = signal.json()["adverse_event_id"]
        details = {
            "trade_name": "Авиандр",
            "description": "Тест: тошнота после таблетки",
            "onset_text": "Сегодня утром",
        }
        assert (
            await client.put(f"/api/v1/me/reaction-signals/{event_id}", json=details, headers=patient)
        ).status_code == 200

        d = await client.post("/api/v1/demo/doctor")
        assert d.status_code == 201, d.text
        doctor = {"Authorization": f"Bearer {d.json()['access_token']}"}
        patients = (await client.get("/api/v1/patients", headers=doctor)).json()
        assert patients["total"] == 3
        assert original["patient_id"] in {p["patient_id"] for p in patients["items"]}
        todo = (await client.get("/api/v1/adverse-events/todo", headers=doctor)).json()
        assert todo[0]["patient_id"] == original["patient_id"]
        assert todo[0]["symptom"] == details["description"]
        repeated_doctor = await client.post("/api/v1/demo/doctor")
        same_doctor = {"Authorization": f"Bearer {repeated_doctor.json()['access_token']}"}
        assert (await client.get("/api/v1/auth/me", headers=same_doctor)).json()["user_id"] == (
            await client.get("/api/v1/auth/me", headers=doctor)
        ).json()["user_id"]
        alerts = (await client.get("/api/v1/alerts", headers=doctor)).json()
        assert alerts["total"] >= 1  # one patient wrote a worrying entry today
    admin = await make_admin(env)
    listed = (await env.client.get("/api/v1/admin/users", headers=admin)).json()
    assert [u["email"] for u in listed["items"]] == ["admin@kompas-demo.ru"]  # demo accounts are hidden


async def test_demo_doctor_first_keeps_linked_patient(env: Env) -> None:
    async with demo_client(env) as client:
        doc = await client.post("/api/v1/demo/doctor")
        doctor = {"Authorization": f"Bearer {doc.json()['access_token']}"}
        p = await client.post("/api/v1/demo/patient")
        patient = {"Authorization": f"Bearer {p.json()['access_token']}"}
        patient_id = (await client.get("/api/v1/auth/me", headers=patient)).json()["patient_id"]
        patients = (await client.get("/api/v1/patients", headers=doctor)).json()
        assert patients["total"] == 3
        assert patient_id in {row["patient_id"] for row in patients["items"]}
