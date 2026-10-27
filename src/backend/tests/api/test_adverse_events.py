"""Adverse events over the API: patient report → alert by severity → doctor review → report flag."""

from datetime import timedelta

from tests.api.conftest import Env, new_id, walk
from tests.api.test_gaps import prescribe, relogin, setup

# NOW = 2026-10-19 09:00 UTC (12:00 Moscow); the prescription below is confirmed at that moment


def report(course_id: str | None = None, **fields: object) -> dict[str, object]:
    return {
        "submission_id": new_id(),
        "course_id": course_id,
        "symptom_code": "nausea",
        "severity": "mild",
        "started_on": "2026-10-19",
        "ongoing": True,
        **fields,
    }


async def test_patient_report_review_and_report_flag(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    course = await prescribe(env, doctor, patient_id, "20:00")
    env.clock.advance(timedelta(days=2))
    doctor, patient = await relogin(env)

    mild = await env.client.post(
        "/api/v1/me/adverse-events", json=report(course, started_on="2026-10-20"), headers=patient
    )
    assert mild.status_code == 201, mild.text
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 0  # mild: notification only
    severe_body = report(course, symptom_code="other", symptom_text="Сильная тревога ночью", severity="severe")
    severe = await env.client.post("/api/v1/me/adverse-events", json=severe_body, headers=patient)
    retry = await env.client.post("/api/v1/me/adverse-events", json=severe_body, headers=patient)
    assert severe.json()["adverse_event_id"] == retry.json()["adverse_event_id"]
    alerts = (await env.client.get("/api/v1/alerts", headers=doctor)).json()
    assert (
        alerts["total"] == 1
        and alerts["items"][0]["level"] == "high"
        and alerts["items"][0]["source"] == "adverse_event"
    )
    kinds = [n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=doctor)).json()]
    assert kinds.count("adverse_event") == 2

    mine = (await env.client.get("/api/v1/me/adverse-events", headers=patient)).json()
    assert {m["symptom"] for m in mine} == {"Тошнота", "Сильная тревога ночью"}
    assert not {k for k, _ in walk(mine)} & {"relation", "doctor_note", "report_required", "status"}

    events = (await env.client.get(f"/api/v1/patients/{patient_id}/adverse-events", headers=doctor)).json()
    assert all(e["status"] == "reported" for e in events)
    assert events[0]["last_therapy_change_on"] == "2026-10-19"  # the prescription was confirmed that day
    event_id = severe.json()["adverse_event_id"]
    no_relation = await env.client.post(
        f"/api/v1/adverse-events/{event_id}/review", json={"status": "confirmed"}, headers=doctor
    )
    assert no_relation.status_code == 422
    confirmed = await env.client.post(
        f"/api/v1/adverse-events/{event_id}/review",
        json={"status": "confirmed", "relation": "probable", "in_label": "yes", "report_required": True},
        headers=doctor,
    )
    assert confirmed.json()["report_required"] is True and confirmed.json()["report_sent_on"] is None
    sent = await env.client.post(
        f"/api/v1/adverse-events/{event_id}/report-sent", json={"sent_on": "2026-10-21"}, headers=doctor
    )
    assert sent.json()["report_sent_on"] == "2026-10-21"
    dismiss = await env.client.post(
        f"/api/v1/adverse-events/{mild.json()['adverse_event_id']}/review", json={"status": "dismissed"}, headers=doctor
    )
    assert dismiss.status_code == 422  # a reason is required
    dismissed = await env.client.post(
        f"/api/v1/adverse-events/{mild.json()['adverse_event_id']}/review",
        json={"status": "dismissed", "note": "Тошнота после еды, связи с терапией нет"},
        headers=doctor,
    )
    assert dismissed.json()["status"] == "dismissed"
    no_report = await env.client.post(
        f"/api/v1/adverse-events/{mild.json()['adverse_event_id']}/report-sent",
        json={"sent_on": "2026-10-21"},
        headers=doctor,
    )
    assert no_report.status_code == 409
    ended = await env.client.post(
        f"/api/v1/me/adverse-events/{event_id}/ended", json={"ended_on": "2026-10-21"}, headers=patient
    )
    assert ended.json()["ongoing"] is False
    actions = {a for (a,) in await env.sql("SELECT action FROM audit_entry WHERE patient_id = :p", p=patient_id)}
    assert {
        "adverse_event.reported",
        "adverse_event.reviewed",
        "adverse_event.report_sent",
        "adverse_event.ended",
    } <= actions


async def test_report_validation_and_access(env: Env) -> None:
    _, patient_id, patient, _ = await setup(env)
    bad = [
        report(symptom_code="nope"),
        report(symptom_code="other"),  # «Другое» needs text
        report(severity="extreme"),
        report(started_on="2026-10-25"),  # future
        report(course_id=new_id()),  # not prescribed
    ]
    for body in bad:
        assert (await env.client.post("/api/v1/me/adverse-events", json=body, headers=patient)).status_code == 422, body
    other = await env.doctor("b@kompas-demo.ru")
    assert (await env.client.get(f"/api/v1/patients/{patient_id}/adverse-events", headers=other)).status_code == 404
    assert (await env.client.get(f"/api/v1/patients/{patient_id}/adverse-events", headers=patient)).status_code == 403
    options = (await env.client.get("/api/v1/adverse-events/options", headers=patient)).json()
    assert {s["code"] for s in options["severities"]} == {"mild", "moderate", "severe"}


async def test_doctor_records_directly(env: Env) -> None:
    doctor, patient_id, _, _ = await setup(env)
    body = {**report(), "relation": "possible", "report_required": True, "note": "Со слов пациента на приёме"}
    r = await env.client.post(f"/api/v1/patients/{patient_id}/adverse-events", json=body, headers=doctor)
    assert r.status_code == 201, r.text
    assert (r.json()["status"], r.json()["reporter_role"], r.json()["report_required"]) == ("confirmed", "doctor", True)
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 0  # the doctor already knows
