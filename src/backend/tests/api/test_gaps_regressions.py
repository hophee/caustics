"""Regressions for the review of gaps 1–4 (2026-10-24)."""

from datetime import timedelta

from kompas.modules.adherence import service as adherence
from tests.api.conftest import Env, new_id
from tests.api.test_gaps import prescribe, relogin, setup


async def test_no_reminders_after_the_drug_is_stopped(env: Env) -> None:
    doctor, patient_id, _, _ = await setup(env)
    course = await prescribe(env, doctor, patient_id, "20:00")
    env.clock.advance(timedelta(hours=8))  # 20:00 Moscow
    assert await adherence.send_due_reminders(env.factory, env.knowledge, now=env.clock.now()) == 1
    env.clock.advance(timedelta(minutes=10))
    doctor, _ = await relogin(env)
    current = (await env.client.get(f"/api/v1/patients/{patient_id}/therapy/current", headers=doctor)).json()
    stop = {"draft": {"new_items": [], "stopped": [course]}, "base_token": current["base_token"]}
    r = await env.client.post(f"/api/v1/patients/{patient_id}/basket/confirm", json=stop, headers=doctor)
    assert r.status_code == 201, r.text
    env.clock.advance(timedelta(minutes=20))  # 20:30: the first repeat would be due
    assert await adherence.send_due_reminders(env.factory, env.knowledge, now=env.clock.now()) == 0


async def test_retrying_an_older_mark_does_not_overwrite_a_newer_one(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    await prescribe(env, doctor, patient_id, "20:00")
    env.clock.advance(timedelta(hours=8, minutes=30))
    _, patient = await relogin(env)
    dose = (await env.client.get("/api/v1/me/doses", headers=patient)).json()[0]
    first = {
        "submission_id": new_id(),
        "course_id": dose["course_id"],
        "scheduled_at": dose["scheduled_at"],
        "status": "taken",
    }
    await env.client.post("/api/v1/me/doses/marks", json=first, headers=patient)
    second = {**first, "submission_id": new_id(), "status": "skipped", "reason": "forgot"}
    changed = await env.client.post("/api/v1/me/doses/marks", json=second, headers=patient)
    assert changed.json()["state"] == "skipped"
    retry = await env.client.post("/api/v1/me/doses/marks", json=first, headers=patient)
    assert retry.json()["state"] == "skipped"  # the old submission is recognised, not re-applied
    assert len(await env.sql("SELECT id FROM dose_mark_submission")) == 2
    naive = {**first, "submission_id": new_id(), "scheduled_at": "2026-10-19T17:00:00"}
    assert (await env.client.post("/api/v1/me/doses/marks", json=naive, headers=patient)).status_code == 422
