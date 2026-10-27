"""Dose marks, reminders, adherence and psychiatrist referrals over the API (no messages: D-120)."""

import dataclasses
import uuid
from datetime import timedelta
from typing import Any

from kompas.core.db import transaction
from kompas.modules.adherence import service as adherence
from kompas.modules.knowledge.care_rules import ReferralRule, ReferralRules
from kompas.modules.referrals import service as referrals
from tests.api.conftest import Env, new_id, walk

# NOW = 2026-10-19 09:00 UTC = 12:00 Moscow


async def setup(env: Env) -> tuple[dict[str, str], str, dict[str, str], str]:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    case_id = await env.case(doctor, patient_id)
    return doctor, patient_id, patient, case_id


async def prescribe(env: Env, doctor: dict[str, str], patient_id: str, *times: str) -> str:
    """Confirm sertraline at the given local times, starting today (2026-10-19) for 28 days."""
    assert env.knowledge.drugs is not None
    label_id = env.knowledge.drugs.by_substance("сертралин")[0].id
    current = (await env.client.get(f"/api/v1/patients/{patient_id}/therapy/current", headers=doctor)).json()
    draft = {
        "new_items": [
            {
                "key": "a",
                "label_id": label_id,
                "origin": {"kind": "search"},
                "regimen": {
                    "intakes": [{"at": t, "amount": 50, "unit": "мг"} for t in times],
                    "start": "2026-10-19",
                    "duration_days": 28,
                },
            }
        ]
    }
    r = await env.client.post(
        f"/api/v1/patients/{patient_id}/basket/confirm",
        json={"draft": draft, "base_token": current["base_token"]},
        headers=doctor,
    )
    assert r.status_code == 201, r.text
    return str(r.json()["items"][0]["course_id"])


async def mark(env: Env, patient: dict[str, str], dose: dict[str, Any], status: str, **extra: Any) -> Any:
    body = {
        "submission_id": new_id(),
        "course_id": dose["course_id"],
        "scheduled_at": dose["scheduled_at"],
        "status": status,
        **extra,
    }
    return await env.client.post("/api/v1/me/doses/marks", json=body, headers=patient)


async def relogin(env: Env) -> tuple[dict[str, str], dict[str, str]]:
    return await env.login("doc@kompas-demo.ru"), await env.login("p@kompas-demo.ru")


# ------------------------------------------------------------------------------- dose marks


async def test_dose_marks_and_adherence_report(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    await prescribe(env, doctor, patient_id, "08:00", "20:00")
    today = (await env.client.get("/api/v1/me/doses", headers=patient)).json()
    # confirmed at 12:00: today's 08:00 dose was never prescribed, only 20:00 remains
    assert [(d["at"], d["state"], d["can_mark"]) for d in today] == [("20:00", "upcoming", False)]
    too_early = await mark(env, patient, today[0], "taken")
    assert too_early.status_code == 422

    env.clock.advance(timedelta(hours=8, minutes=30))  # 20:30 Moscow
    doctor, patient = await relogin(env)
    dose = (await env.client.get("/api/v1/me/doses", headers=patient)).json()[0]
    assert dose["state"] == "due"
    r = await mark(env, patient, dose, "taken")
    assert r.status_code == 200 and r.json()["state"] == "on_time"

    env.clock.advance(timedelta(hours=12))  # 08:30 next day
    doctor, patient = await relogin(env)
    morning = (await env.client.get("/api/v1/me/doses", headers=patient)).json()[0]
    assert (await mark(env, patient, morning, "skipped")).status_code == 422  # reason required
    r = await mark(env, patient, morning, "skipped", reason="intolerance")
    assert r.json()["state"] == "skipped"
    unknown = {**morning, "scheduled_at": "2026-10-20T06:00:00+00:00"}
    assert (await mark(env, patient, unknown, "taken")).status_code == 404

    rep = (await env.client.get(f"/api/v1/patients/{patient_id}/adherence", headers=doctor)).json()
    s = rep["summary"]
    assert (s["on_time"], s["skipped"], s["percent"], s["band"]) == (1, 1, 50.0, "red")
    assert s["skip_reasons"] == {"intolerance": 1} and rep["skip_reason_labels"]["intolerance"] == "Плохо переношу"
    assert rep["courses"][0]["name"]
    actions = {a for (a,) in await env.sql("SELECT action FROM audit_entry WHERE patient_id = :p", p=patient_id)}
    assert "dose.marked" in actions
    # the other doctor can't see it
    other = await env.doctor("b@kompas-demo.ru")
    assert (await env.client.get(f"/api/v1/patients/{patient_id}/adherence", headers=other)).status_code == 404


async def test_reminders_respect_marks_limits_and_are_idempotent(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    await prescribe(env, doctor, patient_id, "20:00")
    rule = (await env.client.get("/api/v1/me/reminders", headers=patient)).json()
    assert rule == {"enabled": True, "first_offset_minutes": 0, "repeat_every_minutes": 30, "max_repeats": 2}
    bad = {**rule, "max_repeats": 9}
    assert (await env.client.put("/api/v1/me/reminders", json=bad, headers=patient)).status_code == 422

    async def send() -> int:
        return await adherence.send_due_reminders(env.factory, env.knowledge, now=env.clock.now())

    assert await send() == 0  # 12:00, nothing due
    env.clock.advance(timedelta(hours=8))  # exactly 20:00 Moscow
    assert await send() == 1
    assert await send() == 0  # same attempt never twice
    env.clock.advance(timedelta(minutes=30))
    assert await send() == 1  # first repeat
    _, patient = await relogin(env)
    dose = (await env.client.get("/api/v1/me/doses", headers=patient)).json()[0]
    await mark(env, patient, dose, "taken")
    env.clock.advance(timedelta(minutes=30))
    assert await send() == 0  # the mark cancelled the last repeat
    kinds = [n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=patient)).json()]
    assert kinds.count("dose_reminder") == 2


async def test_skip_streak_raises_one_high_signal(env: Env) -> None:
    doctor, patient_id, _, _ = await setup(env)
    await prescribe(env, doctor, patient_id, "20:00")
    # 15:00 on the 22nd: the 21st's dose can still be marked (48 h), so only two settled days → no signal
    env.clock.advance(timedelta(days=3, hours=3))
    assert await adherence.check_skip_streaks(env.factory, env.knowledge, now=env.clock.now()) == 0
    env.clock.advance(timedelta(days=2))  # 24th: 19th–21st settled and unmarked
    assert await adherence.check_skip_streaks(env.factory, env.knowledge, now=env.clock.now()) == 1
    env.clock.advance(timedelta(days=7))  # the run continues for days: still one signal (open)
    assert await adherence.check_skip_streaks(env.factory, env.knowledge, now=env.clock.now()) == 1
    rows = await env.sql("SELECT level, source FROM alert")
    assert rows == [("high", "adherence")]


# ------------------------------------------------------------------------------- messages


async def test_no_messages_between_doctor_and_patient(env: Env) -> None:
    """D-120: there is no message channel in either direction."""
    doctor, patient_id, patient, _ = await setup(env)
    body = {"submission_id": new_id(), "text": "Добрый день"}
    assert (await env.client.post("/api/v1/me/messages", json=body, headers=patient)).status_code in (404, 405)
    assert (await env.client.get("/api/v1/me/messages", headers=patient)).status_code == 404
    url = f"/api/v1/patients/{patient_id}/messages"
    assert (await env.client.post(url, json=body, headers=doctor)).status_code in (404, 405)


# ------------------------------------------------------------------------------- referrals


async def test_referral_recommendation_decision_and_issue(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    base = f"/api/v1/patients/{patient_id}"
    rec = (
        await env.client.post(
            f"{base}/referral-recommendations",
            json={"category": "desirable", "reason": "Нет ответа на 2 курса АД", "submission_id": new_id()},
            headers=doctor,
        )
    ).json()
    assert rec["status"] == "open" and rec["category_label"] == "желательно"
    no_reason = await env.client.post(
        f"/api/v1/referral-recommendations/{rec['recommendation_id']}/decision",
        json={"accept": False, "reason": ""},
        headers=doctor,
    )
    assert no_reason.status_code == 422
    declined = await env.client.post(
        f"/api/v1/referral-recommendations/{rec['recommendation_id']}/decision",
        json={"accept": False, "reason": "Пациент наблюдается у психиатра"},
        headers=doctor,
    )
    assert declined.json()["status"] == "declined"
    issue_body = {
        "clinical_reason": "Резистентность к терапии",
        "valid_until": "2026-11-19",
        "recommendation_id": rec["recommendation_id"],
        "submission_id": new_id(),
    }
    assert (await env.client.post(f"{base}/referrals", json=issue_body, headers=doctor)).status_code == 409

    rec2 = (
        await env.client.post(
            f"{base}/referral-recommendations",
            json={"category": "obligatory", "reason": "Суицидальный риск", "submission_id": new_id()},
            headers=doctor,
        )
    ).json()
    body2 = {
        **issue_body,
        "recommendation_id": rec2["recommendation_id"],
        "urgency": "priority",
        "submission_id": new_id(),
    }
    issued = await env.client.post(f"{base}/referrals", json=body2, headers=doctor)
    assert issued.status_code == 201, issued.text
    ref = issued.json()
    retry = await env.client.post(f"{base}/referrals", json=body2, headers=doctor)  # double click / retry
    assert retry.json()["referral_id"] == ref["referral_id"]
    second = await env.client.post(f"{base}/referrals", json={**body2, "submission_id": new_id()}, headers=doctor)
    assert second.status_code == 409  # one referral per recommendation
    assert ref["category"] == "obligatory" and ref["diagnosis_code"] == "F33.2"
    lists = (await env.client.get(f"{base}/referrals", headers=doctor)).json()
    assert {r["status"] for r in lists["recommendations"]} == {"declined", "accepted"}
    mine = (await env.client.get("/api/v1/me/referrals", headers=patient)).json()
    assert mine[0]["specialist"] == "психиатр" and mine[0]["category_label"] == "обязательно"
    assert not {k for k, _ in walk(mine)} & {"clinical_reason", "reason", "recommendation_id"}
    assert "referral" in [n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=patient)).json()]
    past = await env.client.post(
        f"{base}/referrals",
        json={"clinical_reason": "x" * 5, "valid_until": "2026-10-01", "submission_id": new_id()},
        headers=doctor,
    )
    assert past.status_code == 422
    cancelled = await env.client.post(
        f"/api/v1/referrals/{ref['referral_id']}/cancel", json={"reason": "Ошибка в данных"}, headers=doctor
    )
    assert cancelled.json()["status"] == "cancelled"
    assert (await env.client.get(f"{base}/referrals", headers=patient)).status_code == 403


async def test_referral_rules_create_recommendations_once(env: Env) -> None:
    doctor, patient_id, patient, case_id = await setup(env)
    # shipped rules are empty (B02): a severe PHQ-9 recommends nothing
    severe = {"submission_id": new_id(), "scale_code": "PHQ-9", "answers": {str(i): 3 for i in range(1, 10)}}
    await env.client.post("/api/v1/me/responses", json=severe, headers=patient)
    assert (await env.client.get(f"/api/v1/patients/{patient_id}/referrals", headers=doctor)).json()[
        "recommendations"
    ] == []
    rule = ReferralRule(
        code="phq9_severe",
        category="obligatory",
        kind="scale_level",
        scale="PHQ-9",
        min_level=4,
        reason="PHQ-9: тяжёлая депрессия",
    )
    knowledge = dataclasses.replace(env.knowledge, referral_rules=ReferralRules(rules=(rule,)))
    source_id = uuid.uuid4()
    for _ in range(2):
        async with transaction(env.factory) as session:
            await referrals.recommend_from_rules(
                session,
                knowledge,
                patient_id=uuid.UUID(patient_id),
                case_id=uuid.UUID(case_id),
                source="scale:PHQ-9",
                source_id=source_id,
                scale="PHQ-9",
                level=4,
                now=env.clock.now(),
            )
    recs = (await env.client.get(f"/api/v1/patients/{patient_id}/referrals", headers=doctor)).json()["recommendations"]
    assert [(r["rule_code"], r["category"]) for r in recs] == [("phq9_severe", "obligatory")]
    assert "referral_recommendation" in [
        n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=doctor)).json()
    ]
