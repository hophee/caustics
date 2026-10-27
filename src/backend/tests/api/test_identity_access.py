"""Accounts, invites and the access policy (M1, B03): foreign patients look missing (404)."""

from datetime import timedelta

from tests.api.conftest import PASSWORD, Env


async def test_patient_name_parts_round_trip(env: Env) -> None:
    doctor = await env.doctor("names@kompas-demo.ru")
    patient_id, patient = await env.patient(
        doctor, "named@kompas-demo.ru", last_name="Иванов", first_name="Иван", middle_name="Иванович"
    )
    response = await env.client.get("/api/v1/patients", headers=doctor)
    assert response.status_code == 200
    row = next(p for p in response.json()["items"] if p["patient_id"] == patient_id)
    assert (row["last_name"], row["first_name"], row["middle_name"]) == ("Иванов", "Иван", "Иванович")
    assert row["full_name"] == "Иванов Иван Иванович"
    me = await env.client.get("/api/v1/auth/me", headers=patient)
    assert me.json()["first_name"] == "Иван"


async def test_login_rejects_unknown_and_wrong_password_alike(env: Env) -> None:
    await env.doctor("doc@kompas-demo.ru")
    unknown = await env.client.post("/api/v1/auth/login", json={"email": "nobody@kompas-demo.ru", "password": PASSWORD})
    wrong = await env.client.post(
        "/api/v1/auth/login", json={"email": "doc@kompas-demo.ru", "password": "wrong-pass-123"}
    )
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["error"]["message"] == wrong.json()["error"]["message"]


async def test_email_is_case_insensitive(env: Env) -> None:
    await env.doctor("Doc@Kompas-Demo.ru")
    assert await env.login("doc@kompas-demo.RU")


async def test_invite_activation_is_single_use_and_expires(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    r = await env.client.post(
        "/api/v1/patients",
        json={"last_name": "П", "first_name": "Тест", "middle_name": "", "birth_date": "1990-01-01", "sex": "male"},
        headers=doctor,
    )
    code = r.json()["invite_code"]
    ok = await env.client.post(
        "/api/v1/auth/activate", json={"invite_code": code, "email": "p@kompas-demo.ru", "password": PASSWORD}
    )
    assert ok.status_code == 201
    again = await env.client.post(
        "/api/v1/auth/activate", json={"invite_code": code, "email": "p2@kompas-demo.ru", "password": PASSWORD}
    )
    assert again.status_code == 422
    # a fresh card whose code expired
    r = await env.client.post(
        "/api/v1/patients",
        json={"last_name": "Q", "first_name": "Тест", "middle_name": "", "birth_date": "1990-01-01", "sex": "male"},
        headers=doctor,
    )
    env.clock.advance(timedelta(days=15))
    late = await env.client.post(
        "/api/v1/auth/activate",
        json={"invite_code": r.json()["invite_code"], "email": "q@kompas-demo.ru", "password": PASSWORD},
    )
    assert late.status_code == 422
    # the failed activation must not leave a half-created account behind
    assert await env.sql("SELECT 1 FROM user_account WHERE email = 'q@kompas-demo.ru'") == []


async def test_reissued_invite_replaces_the_old_code(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    r = await env.client.post(
        "/api/v1/patients",
        json={"last_name": "П", "first_name": "Тест", "middle_name": "", "birth_date": "1990-01-01", "sex": "male"},
        headers=doctor,
    )
    old, patient_id = r.json()["invite_code"], r.json()["patient_id"]
    new = (await env.client.post(f"/api/v1/patients/{patient_id}/invite", headers=doctor)).json()["invite_code"]
    bad = await env.client.post(
        "/api/v1/auth/activate", json={"invite_code": old, "email": "p@kompas-demo.ru", "password": PASSWORD}
    )
    good = await env.client.post(
        "/api/v1/auth/activate", json={"invite_code": new, "email": "p@kompas-demo.ru", "password": PASSWORD}
    )
    assert (bad.status_code, good.status_code) == (422, 201)


async def test_me_and_patient_list(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru", "Иванова И. И.")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    me = (await env.client.get("/api/v1/auth/me", headers=doctor)).json()
    assert me["role"] == "doctor" and me["full_name"] == "Иванова И. И."
    me = (await env.client.get("/api/v1/auth/me", headers=patient)).json()
    assert me["role"] == "patient" and me["patient_id"] == patient_id
    page = (await env.client.get("/api/v1/patients", headers=doctor)).json()
    assert page["total"] == 1 and page["items"][0]["has_account"] is True


async def test_other_doctors_patient_is_not_found(env: Env) -> None:
    doctor_a = await env.doctor("a@kompas-demo.ru")
    doctor_b = await env.doctor("b@kompas-demo.ru")
    patient_id, _ = await env.patient(doctor_a, "p@kompas-demo.ru")
    case_id = await env.case(doctor_a, patient_id)
    for method, url in [
        ("GET", f"/api/v1/patients/{patient_id}/profile"),
        ("GET", f"/api/v1/patients/{patient_id}/results"),
        ("GET", f"/api/v1/patients/{patient_id}/diary"),
        ("GET", f"/api/v1/patients/{patient_id}/therapy/current"),
        ("POST", f"/api/v1/patients/{patient_id}/invite"),
        ("POST", f"/api/v1/cases/{case_id}/close"),
    ]:
        r = await env.client.request(method, url, headers=doctor_b)
        assert r.status_code == 404, (url, r.text)
    assert (await env.client.get("/api/v1/patients", headers=doctor_b)).json()["total"] == 0


async def test_patient_cannot_call_doctor_endpoints(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    assert (await env.client.get(f"/api/v1/patients/{patient_id}/profile", headers=patient)).status_code == 403
    assert (await env.client.get("/api/v1/alerts", headers=patient)).status_code == 403
    assert (await env.client.get("/api/v1/auth/me")).status_code == 401
    bad = {"Authorization": "Bearer not-a-token"}
    assert (await env.client.get("/api/v1/auth/me", headers=bad)).status_code == 401


async def test_one_active_case(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, _ = await env.patient(doctor, "p@kompas-demo.ru")
    await env.case(doctor, patient_id)
    again = await env.client.post(f"/api/v1/patients/{patient_id}/cases", headers=doctor)
    assert again.status_code == 409


async def test_profile_round_trip_keeps_unknown_vs_none(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, _ = await env.patient(doctor, "p@kompas-demo.ru")
    await env.case(doctor, patient_id)
    profile = (await env.client.get(f"/api/v1/patients/{patient_id}/profile", headers=doctor)).json()
    assert profile["allergies"] is None and profile["pregnant"] == "unknown"
    assert profile["diagnoses"] == ["F33.2"]
    body = {
        "birth_date": "1992-03-01",
        "sex": "female",
        "timezone": "Asia/Novosibirsk",
        "pregnant": "no",
        "lactating": "yes",
        "diagnoses": ["F33.2", "N18"],
        "allergies": [],
        "intolerant_excipients": None,
        "past_intolerance": ["пароксетин"],
        "past_inefficacy": [],
    }
    r = await env.client.put(f"/api/v1/patients/{patient_id}/profile", json=body, headers=doctor)
    assert r.status_code == 200, r.text
    saved = r.json()
    assert saved["allergies"] == [] and saved["intolerant_excipients"] is None
    assert saved["diagnoses"] == ["F33.2", "N18"] and saved["timezone"] == "Asia/Novosibirsk"
    missing = {k: v for k, v in body.items() if k != "allergies"}
    r = await env.client.put(f"/api/v1/patients/{patient_id}/profile", json=missing, headers=doctor)
    assert r.status_code == 422
