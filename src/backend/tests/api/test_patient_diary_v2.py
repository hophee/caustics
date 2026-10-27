"""Patient diary, edit window, and access to own saved clinical data."""

from datetime import timedelta
import pytest
from tests.api.conftest import Env, new_id
from tests.api.test_diary_therapy import setup


def body(**extra):
    return {
        "submission_id": new_id(),
        "day": "2026-10-19",
        "schema_version": 2,
        "mood": 0,
        "anxiety": 3,
        "irritability": 3,
        "sleep_hours": 0,
        "unusual_experiences": True,
        "text": None,
        **extra,
    }


async def test_new_fields_roundtrip_and_only_text_alerts(env: Env):
    doctor, patient_id, patient, _ = await setup(env)
    response = await env.client.post("/api/v1/me/diary", headers=patient, json=body())
    assert response.status_code == 201, response.text
    assert response.json()["sleep_hours"] == 0
    assert response.json()["unusual_experiences"] is True
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 0
    response = await env.client.post("/api/v1/me/diary", headers=patient, json=body(text="Не хочу больше жить"))
    assert response.status_code == 201
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 1
    own = (await env.client.get("/api/v1/me/diary", headers=patient)).json()[0]
    assert own["irritability"] == 3 and "triage" not in own


@pytest.mark.parametrize(
    "field,value",
    [
        ("mood", 3),
        ("anxiety", 4),
        ("irritability", -1),
        ("sleep_hours", 25),
        ("sleep_hours", "8"),
        ("unusual_experiences", "yes"),
        ("mood", True),
    ],
)
async def test_reject_invalid_new_fields(env: Env, field, value):
    _, _, patient, _ = await setup(env)
    response = await env.client.post("/api/v1/me/diary", headers=patient, json=body(**{field: value}))
    assert response.status_code == 422
    assert await env.sql("SELECT id FROM diary_entry_revision") == []


async def test_edit_window_uses_original_creation_time(env: Env):
    _, _, patient, _ = await setup(env)
    initial = body()
    first = await env.client.post("/api/v1/me/diary", headers=patient, json=initial)
    env.clock.advance(timedelta(hours=23))
    patient = await env.login("p@kompas-demo.ru")
    assert (await env.client.post("/api/v1/me/diary", headers=patient, json=body(text="Правка"))).status_code == 201
    env.clock.advance(timedelta(hours=1, seconds=1))
    patient = await env.login("p@kompas-demo.ru")
    denied = await env.client.post("/api/v1/me/diary", headers=patient, json=body(text="Поздняя правка"))
    assert denied.status_code == 403
    retry = await env.client.post("/api/v1/me/diary", headers=patient, json=initial)
    assert retry.json()["revision_id"] == first.json()["revision_id"]
    assert len(await env.sql("SELECT id FROM diary_entry_revision")) == 2


async def test_patient_visits_and_progress_are_own_only(env: Env):
    doctor, _, patient, case_id = await setup(env)
    other_id, other = await env.patient(doctor, "other@kompas-demo.ru")
    await env.case(doctor, other_id)
    content = {"complaints": "Жалоба", "plan": "План", "notes": "Запись врача"}
    saved = await env.client.post(
        f"/api/v1/cases/{case_id}/visits",
        headers=doctor,
        json={"kind": "first", "held_at": env.clock.now().isoformat(), "content": content},
    )
    assert saved.status_code == 201, saved.text
    assert (await env.client.get("/api/v1/me/visits", headers=patient)).json()[0]["content"] == content
    assert (await env.client.get("/api/v1/me/visits", headers=other)).json() == []
    response = await env.client.post(
        "/api/v1/me/responses",
        headers=patient,
        json={"submission_id": new_id(), "scale_code": "PHQ-9", "answers": {str(i): 1 for i in range(1, 10)}},
    )
    assert response.status_code == 201
    assert (await env.client.get("/api/v1/me/progress", headers=patient)).json()[0]["points"][0]["value"] == 9
    assert (await env.client.get("/api/v1/me/progress", headers=other)).json() == []
    assert (await env.client.get("/api/v1/me/responses", headers=other)).json()["total"] == 0


CALM = {"mood": 1, "anxiety": 0, "irritability": 0, "sleep_hours": 8, "unusual_experiences": False}


async def test_v2_safety_question_is_danger_now_and_text_is_check_soon(env: Env):
    doctor, patient_id, patient, _ = await setup(env)
    r = await env.client.post("/api/v1/me/diary", headers=patient, json=body(**CALM, self_harm_thoughts=True))
    assert r.status_code == 201, r.text
    assert "triage" not in r.json() and "scenario" not in r.json()
    text = body(**CALM, day="2026-10-18", self_harm_thoughts=False, text="Не хочу больше жить")
    assert (await env.client.post("/api/v1/me/diary", headers=patient, json=text)).status_code == 201
    alerts = (await env.client.get("/api/v1/alerts", headers=doctor)).json()["items"]
    # «опасность сейчас» first, then «уточнить состояние»
    assert [(a["source"], a["scenario"]) for a in alerts] == [("diary", "danger_now"), ("diary", "check_soon")]
    doc = (await env.client.get(f"/api/v1/patients/{patient_id}/diary", headers=doctor)).json()
    assert {e["day"]: e["triage"]["level"] for e in doc} == {"2026-10-19": "urgent", "2026-10-18": "urgent"}


async def test_v2_skipped_safety_question_needs_attention(env: Env):
    doctor, patient_id, patient, _ = await setup(env)
    assert (await env.client.post("/api/v1/me/diary", headers=patient, json=body(**CALM))).status_code == 201
    doc = (await env.client.get(f"/api/v1/patients/{patient_id}/diary", headers=doctor)).json()
    assert doc[0]["triage"]["level"] == "attention"
    assert [r["code"] for r in doc[0]["triage"]["reasons"]] == ["safety_question_unanswered"]
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 0


async def test_v2_short_sleep_streak_is_found_across_entries(env: Env):
    doctor, patient_id, patient, _ = await setup(env)
    for day, hours in (("2026-10-17", 3.5), ("2026-10-18", 3)):
        night = body(**{**CALM, "sleep_hours": hours}, day=day, self_harm_thoughts=False)
        assert (await env.client.post("/api/v1/me/diary", headers=patient, json=night)).status_code == 201
    doc = {e["day"]: e for e in (await env.client.get(f"/api/v1/patients/{patient_id}/diary", headers=doctor)).json()}
    assert doc["2026-10-17"]["triage"]["level"] == "stable"
    second = doc["2026-10-18"]["triage"]
    assert second["level"] == "attention"
    (reason,) = [r for r in second["reasons"] if r["code"] == "short_sleep_streak"]
    assert "сон 3:30 (17.10); сон 3:00 (18.10)" in reason["message"]
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 0


async def test_v2_rejects_non_boolean_safety_answer(env: Env):
    _, _, patient, _ = await setup(env)
    r = await env.client.post("/api/v1/me/diary", headers=patient, json=body(self_harm_thoughts="yes"))
    assert r.status_code == 422
