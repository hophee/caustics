"""Simple signals keep the original statement and notify before details are available."""

from tests.api.conftest import Env, new_id
from tests.api.test_gaps import setup


async def test_signal_details_and_form(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    body = {"submission_id": new_id()}
    first = await env.client.post("/api/v1/me/reaction-signals", json=body, headers=patient)
    assert first.status_code == 201, first.text
    event_id = first.json()["adverse_event_id"]
    repeat = await env.client.post("/api/v1/me/reaction-signals", json=body, headers=patient)
    assert repeat.json() == first.json()
    notes = (await env.client.get("/api/v1/me/notifications", headers=doctor)).json()
    assert sum(n["kind"] == "adverse_event" for n in notes) == 1
    todo = (await env.client.get("/api/v1/adverse-events/todo", headers=doctor)).json()
    assert todo[0]["patient_id"] == patient_id
    assert todo[0]["details_complete"] is False
    assert todo[0]["started_on"] is None
    pending = (await env.client.get("/api/v1/me/reaction-signals/pending", headers=patient)).json()
    assert pending == [first.json()]
    bad = await env.client.put(
        f"/api/v1/me/reaction-signals/{event_id}",
        json={"trade_name": " ", "description": "x", "onset_text": "вчера"},
        headers=patient,
    )
    assert bad.status_code == 422
    detail = {
        "trade_name": "Трамал",
        "description": "  Тошнит.\nПоявилось желание умереть.  ",
        "onset_text": "Вчера вечером, через час после таблетки",
    }
    saved = await env.client.put(f"/api/v1/me/reaction-signals/{event_id}", json=detail, headers=patient)
    assert saved.status_code == 200, saved.text
    assert (
        await env.client.put(f"/api/v1/me/reaction-signals/{event_id}", json=detail, headers=patient)
    ).status_code == 200
    todo = (await env.client.get("/api/v1/adverse-events/todo", headers=doctor)).json()
    assert todo[0]["symptom"] == detail["description"]
    assert todo[0]["drug_name"] == "Трамал"
    assert todo[0]["onset_text"] == detail["onset_text"]
    assert todo[0]["relation"] is None
    assert (await env.client.get("/api/v1/alerts", headers=doctor)).json()["total"] == 0
    assert (await env.client.get("/api/v1/me/reaction-signals/pending", headers=patient)).json() == []
    form = {"fields": {"drug0_name": "Трамал", "description": "Оценка врача", "contact": "test@example.org"}}
    result = await env.client.put(f"/api/v1/adverse-events/{event_id}/form", json=form, headers=doctor)
    assert result.status_code == 200, result.text
    loaded = (await env.client.get(f"/api/v1/adverse-events/{event_id}/form", headers=doctor)).json()["fields"]
    assert all(loaded[k] == v for k, v in form["fields"].items())
    assert loaded["sex"] == "Ж" and loaded["age"] == "34"
    # Editing the doctor's form must not rewrite the patient's original description.
    assert (await env.client.get("/api/v1/adverse-events/todo", headers=doctor)).json()[0]["symptom"] == detail[
        "description"
    ]
    assert (await env.client.get(f"/api/v1/adverse-events/{event_id}/form", headers=patient)).status_code == 403
    other = await env.doctor("other-fn@example.org")
    assert (await env.client.get(f"/api/v1/adverse-events/{event_id}/form", headers=other)).status_code == 404
    _, other_patient = await env.patient(other, "other-patient-fn@example.org")
    assert (
        await env.client.put(f"/api/v1/me/reaction-signals/{event_id}", json=detail, headers=other_patient)
    ).status_code == 404
    assert (await env.client.get("/api/v1/me/adverse-events", headers=patient)).status_code == 200
