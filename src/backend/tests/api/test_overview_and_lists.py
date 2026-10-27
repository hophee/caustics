"""Read endpoints for the website: the doctor's patient summaries and per-case lists."""

from datetime import datetime, timedelta
from typing import Any

from tests.api.conftest import Env, new_id
from tests.api.test_diary_therapy import entry, label


async def test_patient_summaries_combine_case_diary_alerts(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    await env.case(doctor, patient_id, icd="F41.1")
    r = await env.client.post("/api/v1/me/diary", json=entry(day="2026-10-19"), headers=patient)
    assert r.status_code == 201, r.text
    phq = {"submission_id": new_id(), "scale_code": "PHQ-9", "answers": {str(i): 0 for i in range(1, 9)} | {"9": 1}}
    assert (await env.client.post("/api/v1/me/responses", json=phq, headers=patient)).status_code == 201
    other_id, _ = await env.patient(
        doctor, "q@kompas-demo.ru", last_name="Борис", first_name="Без", middle_name="Случая"
    )

    page = (await env.client.get("/api/v1/overview/patients?days=7", headers=doctor)).json()
    assert page["total"] == 2
    rows: dict[str, Any] = {row["patient_id"]: row for row in page["items"]}
    anna = rows[patient_id]
    assert anna["main_icd"] == "F41.1" and anna["case_status"] == "active" and anna["has_account"]
    assert anna["sex"] == "female" and anna["last_entry_at"] is not None
    assert len(anna["days"]) == 7 and anna["days"][-1]["day"] == "2026-10-19"
    assert anna["days"][-1]["level"] == "stable" and not anna["days"][-1]["pending"]
    assert all(d["level"] is None for d in anna["days"][:-1])  # missing days stay missing (A12)
    assert anna["open_alerts"] == 1 and anna["top_alert_level"] == "urgent"
    assert anna["therapy"] == []
    boris = rows[other_id]
    assert boris["case_id"] is None and boris["main_icd"] is None and boris["open_alerts"] == 0


async def test_patient_summaries_are_scoped_to_the_treating_doctor(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    stranger = await env.doctor("other@kompas-demo.ru", name="Другой Врач")
    await env.patient(doctor, "p@kompas-demo.ru")
    assert (await env.client.get("/api/v1/overview/patients", headers=stranger)).json()["total"] == 0
    _, patient = await env.patient(doctor, "q@kompas-demo.ru")
    assert (await env.client.get("/api/v1/overview/patients", headers=patient)).status_code == 403


async def test_one_patient_summary(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    stranger = await env.doctor("other@kompas-demo.ru", name="Другой Врач")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    await env.case(doctor, patient_id, icd="F33.2")

    r = await env.client.get(f"/api/v1/overview/patients/{patient_id}?days=3", headers=doctor)
    assert r.status_code == 200, r.text
    one = r.json()
    assert one["patient_id"] == patient_id and one["main_icd"] == "F33.2" and len(one["days"]) == 3
    # foreign doctors get 404 (no existence leak); patients can't read doctor summaries
    assert (await env.client.get(f"/api/v1/overview/patients/{patient_id}", headers=stranger)).status_code == 404
    assert (await env.client.get(f"/api/v1/overview/patients/{patient_id}", headers=patient)).status_code == 403
    assert (await env.client.get(f"/api/v1/overview/patients/{new_id()}", headers=doctor)).status_code == 404


async def test_summary_dates_use_held_visits_and_patient_actions(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    case_id = await env.case(doctor, patient_id)
    env.clock.advance(timedelta(minutes=1))
    activity_at = env.clock.now()
    r = await env.client.post(
        "/api/v1/me/messages", json={"submission_id": new_id(), "text": "Спасибо"}, headers=patient
    )
    assert r.status_code == 201, r.text
    env.clock.advance(timedelta(hours=1))
    doctor = await env.login("doc@kompas-demo.ru")
    # The latest recorded visit is in the future; neither it nor the doctor's actions count as activity.
    for held_at in ("2026-10-18T08:00:00Z", "2026-10-19T08:00:00Z", "2026-10-20T08:00:00Z"):
        r = await env.client.post(
            f"/api/v1/cases/{case_id}/visits", json={"kind": "repeat", "held_at": held_at}, headers=doctor
        )
        assert r.status_code == 201, r.text
    other = await env.client.post(
        "/api/v1/patients",
        json={
            "last_name": "Без",
            "first_name": "активности",
            "middle_name": "",
            "birth_date": "1990-01-01",
            "sex": "male",
        },
        headers=doctor,
    )
    assert other.status_code == 201, other.text

    page = (await env.client.get("/api/v1/overview/patients", headers=doctor)).json()
    rows = {row["patient_id"]: row for row in page["items"]}
    assert datetime.fromisoformat(rows[patient_id]["last_activity_at"]) == activity_at
    assert datetime.fromisoformat(rows[patient_id]["last_visit_at"]) == datetime.fromisoformat("2026-10-19T08:00:00Z")
    assert rows[other.json()["patient_id"]]["last_activity_at"] is None
    assert rows[other.json()["patient_id"]]["last_visit_at"] is None


async def test_request_validation_errors_use_the_error_shape(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    body = {"last_name": "", "first_name": "Тест", "middle_name": "", "birth_date": "x", "sex": "f"}
    r = await env.client.post("/api/v1/patients", json=body, headers=doctor)
    assert r.status_code == 422
    error = r.json()["error"]
    assert error["code"] == "invalid_input"
    assert {d["pointer"] for d in error["details"]} >= {"last_name", "birth_date", "sex"}


async def test_alerts_carry_sent_and_delivered_times(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    await env.case(doctor, patient_id, icd="F33.2")
    phq = {"submission_id": new_id(), "scale_code": "PHQ-9", "answers": {str(i): 0 for i in range(1, 9)} | {"9": 1}}
    assert (await env.client.post("/api/v1/me/responses", json=phq, headers=patient)).status_code == 201
    alert = (await env.client.get("/api/v1/alerts", headers=doctor)).json()["items"][0]
    assert alert["sent_at"] is None and alert["delivered_at"] is None  # no worker here: not dispatched
    # accepted before dispatch, then a late delivery ack: the time is recorded, the status kept
    assert (await env.client.post(f"/api/v1/alerts/{alert['alert_id']}/accept", headers=doctor)).status_code == 200
    r = await env.client.post(f"/api/v1/alerts/{alert['alert_id']}/delivered", headers=doctor)
    assert r.status_code == 200
    assert r.json()["delivered_at"] is not None and r.json()["status"] == "accepted"


async def test_case_lists_visits_diagnoses_assignments(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, _ = await env.patient(doctor, "p@kompas-demo.ru")
    case_id = await env.case(doctor, patient_id)
    visit = {"kind": "first", "held_at": "2026-10-19T08:00:00Z", "content": {"complaints": "сниженное настроение"}}
    assert (await env.client.post(f"/api/v1/cases/{case_id}/visits", json=visit, headers=doctor)).status_code == 201
    assign = {"scale_code": "PHQ-9", "mode": "repeat", "every_days": 14, "starts_on": "2026-10-19"}
    assert (
        await env.client.post(f"/api/v1/cases/{case_id}/assignments", json=assign, headers=doctor)
    ).status_code == 201

    visits = (await env.client.get(f"/api/v1/cases/{case_id}/visits", headers=doctor)).json()
    assert [v["kind"] for v in visits] == ["first"] and visits[0]["content"]["complaints"] == "сниженное настроение"
    diagnoses = (await env.client.get(f"/api/v1/cases/{case_id}/diagnoses", headers=doctor)).json()
    assert [(d["icd_code"], d["is_main"]) for d in diagnoses] == [("F33.2", True)]
    assignments = (await env.client.get(f"/api/v1/cases/{case_id}/assignments", headers=doctor)).json()
    assert [(a["scale_code"], a["every_days"]) for a in assignments] == [("PHQ-9", 14)]

    stranger = await env.doctor("other@kompas-demo.ru", name="Другой Врач")
    for path in ("visits", "diagnoses", "assignments"):
        assert (await env.client.get(f"/api/v1/cases/{case_id}/{path}", headers=stranger)).status_code == 404


async def test_drug_card_is_doctor_only(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    _, patient = await env.patient(doctor, "p@kompas-demo.ru")
    label_id = label(env, "амитриптилин")
    card = (await env.client.get(f"/api/v1/drugs/{label_id}", headers=doctor)).json()
    assert card["id"] == label_id and card["display_name"] and "lactation" in card and "dose" in card
    assert (await env.client.get(f"/api/v1/drugs/{label_id}", headers=patient)).status_code == 403
    assert (await env.client.get("/api/v1/drugs/unknown", headers=doctor)).status_code == 404
