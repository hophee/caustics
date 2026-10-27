"""Contact persistence, access policy and the limit under concurrent requests."""

import asyncio

from .conftest import Env

BODY = dict(last_name="Иванов", first_name="Иван", role="Брат", phone="8 (999) 123-45-67")
URL = "/api/v1/me/emergency-contacts"


async def test_emergency_contacts(env: Env) -> None:
    doctor = await env.doctor("doctor@example.test")
    other_doctor = await env.doctor("other@example.test")
    patient_id, patient = await env.patient(doctor, "patient@example.test")
    _, other_patient = await env.patient(doctor, "other-patient@example.test")
    doctor_url = f"/api/v1/patients/{patient_id}/emergency-contacts"
    assert (await env.client.get(doctor_url, headers=doctor)).json() == []
    assert (await env.client.post(URL, json=BODY, headers=doctor)).status_code == 403
    assert (await env.client.get(doctor_url, headers=other_doctor)).status_code == 404
    assert (await env.client.get(doctor_url, headers=other_patient)).status_code == 403
    assert (await env.client.post(URL, json={**BODY, "role": "x" * 16}, headers=patient)).status_code == 422

    responses = await asyncio.gather(*(
        env.client.post(URL, json={**BODY, "first_name": f"Имя{i}"}, headers=patient) for i in range(6)
    ))
    assert sorted(r.status_code for r in responses) == [201, 201, 201, 201, 201, 409]
    saved = (await env.client.get(URL, headers=patient)).json()
    assert len(saved) == 5
    assert all(c["phone"] == "89991234567" for c in saved)
    assert (await env.client.get(doctor_url, headers=doctor)).json() == saved
    assert (await env.client.get(URL, headers=other_patient)).json() == []
    assert (await env.client.post(doctor_url, json=BODY, headers=doctor)).status_code == 405
