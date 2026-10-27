"""«Вы часто назначаете»: the doctor's most frequent new picks from confirmed prescriptions."""

from typing import Any

from tests.api.conftest import Env
from tests.api.test_diary_therapy import draft_item, label
from tests.api.test_gaps import prescribe, setup


async def confirm(env: Env, doctor: dict[str, str], patient_id: str, substance: str) -> None:
    current = (await env.client.get(f"/api/v1/patients/{patient_id}/therapy/current", headers=doctor)).json()
    body: dict[str, Any] = {
        "draft": {"new_items": [draft_item("b", label(env, substance), 25)], "stopped": []},
        "base_token": current["base_token"],
        "justifications": {},
    }
    r = await env.client.post(f"/api/v1/patients/{patient_id}/basket/confirm", json=body, headers=doctor)
    if r.status_code == 422:  # red warnings need a written justification (D-046)
        needed = [p["pointer"] for p in r.json()["error"]["details"] if p["code"] == "justification_required"]
        body["justifications"] = {wid: "Обсуждено с пациентом, контроль через неделю" for wid in needed}
        r = await env.client.post(f"/api/v1/patients/{patient_id}/basket/confirm", json=body, headers=doctor)
    assert r.status_code == 201, r.text


async def test_frequent_picks(env: Env) -> None:
    doctor, patient_id, _, _ = await setup(env)
    assert (await env.client.get("/api/v1/therapy/frequent", headers=doctor)).json() == []
    await prescribe(env, doctor, patient_id, "08:00")
    second_id, second = await env.patient(doctor, "p2@kompas-demo.ru")
    await env.case(doctor, second_id)
    await prescribe(env, doctor, second_id, "09:00")
    await confirm(env, doctor, patient_id, "миртазапин")  # sertraline is carried over, not a new pick

    picks = (await env.client.get("/api/v1/therapy/frequent", headers=doctor)).json()
    assert [(p["substances"], p["times"]) for p in picks] == [(["сертралин"], 2), (["миртазапин"], 1)]
    assert all(p["provenance"] is None for p in picks)
    for_patient = (await env.client.get(f"/api/v1/therapy/frequent?patient_id={patient_id}", headers=doctor)).json()
    assert all(p["provenance"]["kr_status"] for p in for_patient)

    other = await env.doctor("other@kompas-demo.ru")
    assert (await env.client.get("/api/v1/therapy/frequent", headers=other)).json() == []  # per doctor
    assert (await env.client.get(f"/api/v1/therapy/frequent?patient_id={patient_id}", headers=other)).status_code == 404
    assert (await env.client.get("/api/v1/therapy/frequent", headers=second)).status_code == 403
    assert len((await env.client.get("/api/v1/therapy/frequent?limit=1", headers=doctor)).json()) == 1
