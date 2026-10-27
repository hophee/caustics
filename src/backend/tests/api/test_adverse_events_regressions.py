"""Regressions for the adverse-event review (2026-10-24)."""

from tests.api.conftest import Env
from tests.api.test_adverse_events import report
from tests.api.test_gaps import setup


async def test_finished_symptom_needs_an_end_date(env: Env) -> None:
    _, _, patient, _ = await setup(env)
    no_end = report(ongoing=False)
    assert (await env.client.post("/api/v1/me/adverse-events", json=no_end, headers=patient)).status_code == 422
    ok = await env.client.post("/api/v1/me/adverse-events", json={**no_end, "ended_on": "2026-10-19"}, headers=patient)
    assert ok.status_code == 201 and ok.json()["ended_on"] == "2026-10-19"


async def test_sent_report_is_final_and_dated_sensibly(env: Env) -> None:
    doctor, patient_id, _, _ = await setup(env)
    body = {**report(started_on="2026-10-18"), "relation": "probable", "report_required": True}
    event = (await env.client.post(f"/api/v1/patients/{patient_id}/adverse-events", json=body, headers=doctor)).json()
    url = f"/api/v1/adverse-events/{event['adverse_event_id']}"
    todo = (await env.client.get("/api/v1/adverse-events/todo", headers=doctor)).json()
    assert [e["adverse_event_id"] for e in todo] == [event["adverse_event_id"]]
    future = await env.client.post(f"{url}/report-sent", json={"sent_on": "2026-10-25"}, headers=doctor)
    before = await env.client.post(f"{url}/report-sent", json={"sent_on": "2026-10-17"}, headers=doctor)
    assert (future.status_code, before.status_code) == (422, 422)
    assert (
        await env.client.post(f"{url}/report-sent", json={"sent_on": "2026-10-19"}, headers=doctor)
    ).status_code == 200
    again = await env.client.post(f"{url}/report-sent", json={"sent_on": "2026-10-19"}, headers=doctor)
    assert again.status_code == 409
    dismiss = await env.client.post(f"{url}/review", json={"status": "dismissed", "note": "передумал"}, headers=doctor)
    assert dismiss.status_code == 409
    assert (await env.client.get("/api/v1/adverse-events/todo", headers=doctor)).json() == []


async def test_doctor_replay_must_match_the_patient(env: Env) -> None:
    doctor, patient_a, _, _ = await setup(env)
    patient_b, _ = await env.patient(doctor, "p2@kompas-demo.ru")
    await env.case(doctor, patient_b)
    body = {**report(), "relation": "possible"}
    assert (
        await env.client.post(f"/api/v1/patients/{patient_a}/adverse-events", json=body, headers=doctor)
    ).status_code == 201
    reused = await env.client.post(f"/api/v1/patients/{patient_b}/adverse-events", json=body, headers=doctor)
    assert reused.status_code == 409


async def test_patient_cannot_end_another_patients_event(env: Env) -> None:
    doctor, _, patient, _ = await setup(env)
    event = (await env.client.post("/api/v1/me/adverse-events", json=report(), headers=patient)).json()
    _, other = await env.patient(doctor, "p2@kompas-demo.ru")
    r = await env.client.post(
        f"/api/v1/me/adverse-events/{event['adverse_event_id']}/ended", json={"ended_on": "2026-10-19"}, headers=other
    )
    assert r.status_code == 404
