"""Regressions for the persistence/API review (2026-10-24)."""

import asyncio
import uuid
from datetime import timedelta

from kompas.core import outbox
from kompas.core.db import transaction
from kompas.modules.notifications import service as notifications
from tests.api.conftest import PASSWORD, Env, new_id


async def test_critical_answer_in_closed_case_still_alerts(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    case_id = await env.case(doctor, patient_id)
    await env.client.post(f"/api/v1/cases/{case_id}/close", headers=doctor)
    answers = {str(i): 0 for i in range(1, 9)} | {"9": 3}
    r = await env.client.post(
        "/api/v1/me/responses",
        json={"submission_id": new_id(), "scale_code": "PHQ-9", "answers": answers, "assignment_id": new_id()},
        headers=patient,
    )
    assert r.status_code == 422
    codes = {p["code"] for p in r.json()["error"]["details"]}
    assert {"case_closed", "assignment_mismatch"} <= codes
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 1


async def test_activate_does_not_reveal_registered_emails(env: Env) -> None:
    await env.doctor("doc@kompas-demo.ru")
    r = await env.client.post(
        "/api/v1/auth/activate",
        json={"invite_code": "WRONGCODE123", "email": "doc@kompas-demo.ru", "password": PASSWORD},
    )
    assert r.status_code == 422 and r.json()["error"]["details"][0]["code"] == "invite_invalid"


async def test_message_that_kills_workers_ends_dead(env: Env) -> None:
    async with transaction(env.factory) as session:
        await outbox.enqueue(session, "test.crash", {}, now=env.clock.now(), max_attempts=2)
    for _ in range(3):  # claim, then "die" (never complete/fail), lease expires
        async with transaction(env.factory) as session:
            await outbox.claim(session, topics=["test.crash"], now=env.clock.now(), lease_seconds=60, limit=1)
        env.clock.advance(timedelta(hours=1))
    async with transaction(env.factory) as session:
        await outbox.claim(session, topics=["test.crash"], now=env.clock.now(), lease_seconds=60, limit=1)
    status, attempts, error = (await env.sql("SELECT status, attempts, last_error FROM outbox_message"))[0]
    assert status == "dead" and attempts == 2 and "lease expired" in error


async def test_diary_text_is_not_copied_into_the_outbox(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    await env.case(doctor, patient_id)
    body = {
        "submission_id": new_id(),
        "day": "2026-10-19",
        "self_harm_thoughts": False,
        "text": "Секретный текст пациента",
    }
    assert (await env.client.post("/api/v1/me/diary", json=body, headers=patient)).status_code == 201
    payloads = [
        p for (p,) in await env.sql("SELECT payload::text FROM outbox_message WHERE topic = 'diary.model_triage'")
    ]
    assert payloads and all("Секретный" not in p for p in payloads)
    await env.run_worker()
    doc = (await env.client.get(f"/api/v1/patients/{patient_id}/diary", headers=doctor)).json()
    assert doc[0]["triage"]["model_pending"] is False


async def test_alert_retry_re_emits_the_notification_on_open_streams(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    user_id = uuid.UUID((await env.client.get("/api/v1/auth/me", headers=doctor)).json()["user_id"])
    async with transaction(env.factory) as session:
        note = await notifications.notify(session, user_id, "alert", {"x": 1}, now=env.clock.now())
    hub = notifications.NotificationHub(env.settings.database_url)
    await hub.start()
    try:
        events = notifications.stream(hub, env.factory, note.user_id, last_event_id=note.seq)
        first = asyncio.ensure_future(anext(events))
        await asyncio.sleep(0.5)  # stream is now waiting
        async with transaction(env.factory) as session:
            await notifications.renotify(session, note.id)
        got = await asyncio.wait_for(first, timeout=10)
        assert got is not None and got.id == note.id
        await events.aclose()
    finally:
        await hub.stop()
