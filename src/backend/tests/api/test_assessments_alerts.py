"""Scales end to end: patient payloads carry no result in any form (D-109), critical answers raise
alerts that survive rejected submissions, the worker delivers them, and the doctor's lifecycle
actions are audited."""

from datetime import timedelta
from typing import Any

from tests.api.conftest import Env, new_id, walk

PATIENT_FORBIDDEN = {
    "total",
    "band",
    "subscores",
    "answers",
    "critical_hits",
    "scenario",
    "explanation",
    "value",
    "triage",
    "view",
    "texts",
    "status_text",
    "disclaimer",
    "doctor_comment",
    "level",
    "result_id",
}


def phq9(*values: int) -> dict[str, int]:
    return {str(i + 1): v for i, v in enumerate(values)}


def assert_no_result(payload: Any) -> None:
    keys = {k for k, _ in walk(payload)}
    assert not keys & PATIENT_FORBIDDEN, keys & PATIENT_FORBIDDEN


async def setup(env: Env) -> tuple[dict[str, str], str, dict[str, str], str]:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    case_id = await env.case(doctor, patient_id)
    return doctor, patient_id, patient, case_id


async def test_patient_sees_saved_only_and_doctor_sees_numbers(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    body = {"submission_id": new_id(), "scale_code": "PHQ-9", "answers": phq9(1, 1, 1, 1, 1, 1, 1, 2, 0)}
    r = await env.client.post("/api/v1/me/responses", json=body, headers=patient)
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"response_id", "status"} and r.json()["status"] == "saved"
    history = (await env.client.get("/api/v1/me/responses", headers=patient)).json()
    assert history["total"] == 1
    assert history["items"][0]["total"] == 9
    assert history["items"][0]["band"]["label"]
    assert not {"answers", "critical_hits", "explanation"} & set(history["items"][0])
    for gone in ("/api/v1/me/results",):
        assert (await env.client.get(gone, headers=patient)).status_code in (404, 405)
    doc = (await env.client.get(f"/api/v1/patients/{patient_id}/results", headers=doctor)).json()["items"][0]
    assert doc["total"] == 9 and doc["band"]["code"] and doc["answers"]["8"] == 2
    series = (await env.client.get(f"/api/v1/patients/{patient_id}/progress", headers=doctor)).json()
    assert series[0]["scale_code"] == "PHQ-9" and series[0]["points"][0]["value"] == 9


async def test_retry_with_same_submission_id_is_idempotent(env: Env) -> None:
    _, _, patient, _ = await setup(env)
    body = {"submission_id": new_id(), "scale_code": "PHQ-9", "answers": phq9(0, 0, 0, 0, 0, 0, 0, 0, 2)}
    first = await env.client.post("/api/v1/me/responses", json=body, headers=patient)
    second = await env.client.post("/api/v1/me/responses", json=body, headers=patient)
    assert first.json() == second.json()
    assert len(await env.sql("SELECT id FROM response")) == 1
    assert len(await env.sql("SELECT id FROM alert")) == 1


async def test_critical_answer_alerts_even_when_rejected(env: Env) -> None:
    doctor, _, patient, _ = await setup(env)
    body = {"submission_id": new_id(), "scale_code": "PHQ-9", "answers": {"9": 3}}  # incomplete
    r = await env.client.post("/api/v1/me/responses", json=body, headers=patient)
    assert r.status_code == 422
    assert {p["pointer"] for p in r.json()["error"]["details"]}  # points at the missing answers
    assert [s for (s,) in await env.sql("SELECT status FROM response")] == ["rejected"]
    alerts = (await env.client.get("/api/v1/alerts", headers=doctor)).json()
    assert alerts["total"] == 1 and alerts["items"][0]["level"] == "urgent"
    assert alerts["items"][0]["scenario"] == "danger_now"  # a questionnaire answer (D-132)
    assert "пункт 9" in alerts["items"][0]["reason"]


async def test_alert_delivery_retry_and_lifecycle(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    body = {"submission_id": new_id(), "scale_code": "PHQ-9", "answers": phq9(1, 1, 1, 1, 1, 1, 1, 1, 1)}
    await env.client.post("/api/v1/me/responses", json=body, headers=patient)
    assert await env.run_worker() == 1
    alert = (await env.client.get("/api/v1/alerts", headers=doctor)).json()["items"][0]
    assert alert["status"] == "sent"
    notes = (await env.client.get("/api/v1/me/notifications", headers=doctor)).json()
    assert [n["kind"] for n in notes] == ["alert"] and notes[0]["payload"]["alert_id"] == alert["alert_id"]
    # nothing acknowledged: retries re-send the same notification, then flag it undelivered
    for _ in range(env.settings.alert_retry_max_attempts + 1):
        env.clock.advance(timedelta(minutes=16))
        await env.run_worker()
    assert len(await env.sql("SELECT id FROM notification")) == 1
    doctor = await env.login("doc@kompas-demo.ru")  # the clock moved past the token lifetime
    alert = (await env.client.get("/api/v1/alerts", headers=doctor)).json()["items"][0]
    assert alert["undelivered"] is True
    alert_id = alert["alert_id"]
    r = await env.client.post(f"/api/v1/alerts/{alert_id}/delivered", headers=doctor)
    assert r.json()["status"] == "delivered" and r.json()["undelivered"] is False
    r = await env.client.post(f"/api/v1/alerts/{alert_id}/accept", headers=doctor)
    assert r.json()["status"] == "accepted"
    empty = await env.client.post(f"/api/v1/alerts/{alert_id}/handle", json={"note": ""}, headers=doctor)
    assert empty.status_code == 422
    r = await env.client.post(
        f"/api/v1/alerts/{alert_id}/handle", json={"note": "Позвонила, риск низкий"}, headers=doctor
    )
    assert r.json()["status"] == "handled"
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 0
    actions = {a for (a,) in await env.sql("SELECT action FROM audit_entry WHERE patient_id = :p", p=patient_id)}
    assert {"response.submitted", "alert.accepted", "alert.handled"} <= actions


async def test_clinician_rating_stays_with_the_doctor(env: Env) -> None:
    doctor, patient_id, patient, case_id = await setup(env)
    body = {"submission_id": new_id(), "scale_code": "HDRS-17", "answers": {str(i): 1 for i in range(1, 18)}}
    r = await env.client.post(f"/api/v1/cases/{case_id}/ratings", json=body, headers=doctor)
    assert r.status_code == 201, r.text
    assert r.json()["total"] == 17 and "publication_status" not in r.json()
    result_id = r.json()["result_id"]
    publish = await env.client.post(f"/api/v1/results/{result_id}/publish", json={}, headers=doctor)
    assert publish.status_code in (404, 405)
    history = (await env.client.get("/api/v1/me/responses", headers=patient)).json()
    assert history["total"] == 0  # a clinician rating is not part of the patient's history
    assert (await env.client.get("/api/v1/me/notifications", headers=patient)).json() == []
    doc = (await env.client.get(f"/api/v1/patients/{patient_id}/results", headers=doctor)).json()
    assert doc["items"][0]["total"] == 17


async def test_doctor_rating_raises_no_alert(env: Env) -> None:
    doctor, _, _, case_id = await setup(env)
    body = {"submission_id": new_id(), "scale_code": "C-SSRS", "answers": {"1": 5, "2": 1}}
    r = await env.client.post(f"/api/v1/cases/{case_id}/ratings", json=body, headers=doctor)
    assert r.status_code == 201, r.text
    assert r.json()["critical_hits"]  # the doctor sees the critical items in the result (D-119)
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 0


async def test_roles_and_scales_must_match(env: Env) -> None:
    doctor, _, patient, case_id = await setup(env)
    hdrs = {"submission_id": new_id(), "scale_code": "HDRS-17", "answers": {str(i): 0 for i in range(1, 18)}}
    assert (await env.client.post("/api/v1/me/responses", json=hdrs, headers=patient)).status_code == 403
    phq = {"submission_id": new_id(), "scale_code": "PHQ-9", "answers": phq9(0, 0, 0, 0, 0, 0, 0, 0, 0)}
    assert (await env.client.post(f"/api/v1/cases/{case_id}/ratings", json=phq, headers=doctor)).status_code == 403
    unknown = {**phq, "submission_id": new_id(), "scale_code": "NOPE"}
    assert (await env.client.post("/api/v1/me/responses", json=unknown, headers=patient)).status_code == 404


async def test_assignments_become_due(env: Env) -> None:
    doctor, _, patient, case_id = await setup(env)
    body = {"scale_code": "PHQ-9", "mode": "repeat", "every_days": 7, "starts_on": "2026-10-19"}
    r = await env.client.post(f"/api/v1/cases/{case_id}/assignments", json=body, headers=doctor)
    assert r.status_code == 201, r.text
    assignment_id = r.json()["assignment_id"]
    due = (await env.client.get("/api/v1/me/assignments", headers=patient)).json()
    assert [a["assignment_id"] for a in due] == [assignment_id]
    answer = {
        "submission_id": new_id(),
        "scale_code": "PHQ-9",
        "answers": phq9(0, 0, 0, 0, 0, 0, 0, 0, 0),
        "assignment_id": assignment_id,
    }
    await env.client.post("/api/v1/me/responses", json=answer, headers=patient)
    assert (await env.client.get("/api/v1/me/assignments", headers=patient)).json() == []
    env.clock.advance(timedelta(days=7))
    doctor, patient = await env.login("doc@kompas-demo.ru"), await env.login("p@kompas-demo.ru")
    assert len((await env.client.get("/api/v1/me/assignments", headers=patient)).json()) == 1
    clinician = {**body, "scale_code": "HDRS-17"}
    assert (
        await env.client.post(f"/api/v1/cases/{case_id}/assignments", json=clinician, headers=doctor)
    ).status_code == 422


async def test_transfer_moves_alerts_and_access(env: Env) -> None:
    doctor_a, patient_id, patient, _ = await setup(env)
    doctor_b = await env.doctor("b@kompas-demo.ru")
    b_id = (await env.client.get("/api/v1/auth/me", headers=doctor_b)).json()["doctor_id"]
    body = {"submission_id": new_id(), "scale_code": "PHQ-9", "answers": phq9(0, 0, 0, 0, 0, 0, 0, 0, 3)}
    await env.client.post("/api/v1/me/responses", json=body, headers=patient)
    await env.run_worker()
    r = await env.client.post(f"/api/v1/patients/{patient_id}/transfer", json={"new_doctor_id": b_id}, headers=doctor_a)
    assert r.status_code == 200, r.text
    assert (await env.client.get("/api/v1/alerts", headers=doctor_a)).json()["total"] == 0
    assert (await env.client.get("/api/v1/me/notifications", headers=doctor_a)).json() == []
    assert (await env.client.get(f"/api/v1/patients/{patient_id}/profile", headers=doctor_a)).status_code == 404
    assert (await env.client.get("/api/v1/alerts", headers=doctor_b)).json()["total"] == 1
    assert [n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=doctor_b)).json()] == ["alert"]
