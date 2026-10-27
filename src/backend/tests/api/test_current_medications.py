from tests.api.conftest import Env, new_id
from tests.api.test_diary_therapy import draft_item, label, setup


async def test_full_directory_finds_and_saves_non_psychiatric_medication(env: Env) -> None:
    doctor, pid, _, _ = await setup(env)
    found = await env.client.get("/api/v1/medications/search", params={"q": "ТРАМАДОЛ АВЕКСИМА"}, headers=doctor)
    assert found.status_code == 200
    option = found.json()["items"][0]
    assert option["substance"] == "трамадол" and option["label_id"] is None
    by_mnn = await env.client.get("/api/v1/medications/search", params={"q": "трамадол"}, headers=doctor)
    assert by_mnn.json()["items"][0]["code"] == "mnn:трамадол"
    url = f"/api/v1/patients/{pid}/therapy/current"
    current = (await env.client.get(url, headers=doctor)).json()
    saved = await env.client.put(
        url,
        headers=doctor,
        json={
            "base_token": current["base_token"],
            "items": [{"substance": option["substance"], "label_id": option["label_id"]}],
        },
    )
    assert saved.status_code == 200, saved.text
    assert (await env.client.get(url, headers=doctor)).json()["items"][0]["substance"] == "трамадол"
    checked = await env.client.post(
        f"/api/v1/patients/{pid}/basket/check",
        headers=doctor,
        json={"new_items": [draft_item("new", label(env, "сертралин"), 50)], "stopped": []},
    )
    assert any("interaction" in w["code"] for w in checked.json()["warnings"])
    # A name in the full directory is not a fabricated parsed safety label.
    assert not env.knowledge.drugs.by_substance("трамадол")


async def test_medications_before_first_case_are_carried_into_visit(env: Env) -> None:
    doctor = await env.doctor("new-card@kompas-demo.ru")
    pid, _ = await env.patient(doctor, "new-card-patient@kompas-demo.ru")
    url = f"/api/v1/patients/{pid}/therapy/current"
    initial = await env.client.get(url, headers=doctor)
    assert initial.status_code == 200 and initial.json()["items"] == []
    sid = label(env, "сертралин")
    saved = await env.client.put(
        url,
        headers=doctor,
        json={
            "base_token": initial.json()["base_token"],
            "items": [{"label_id": sid}],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["items"][0]["substance"] == "сертралин"
    assert (await env.client.get(f"/api/v1/patients/{pid}/cases/active", headers=doctor)).status_code == 404
    assert (await env.client.get(url, headers=doctor)).json() == saved.json()
    cid = await env.case(doctor, pid)
    current = (await env.client.get(url, headers=doctor)).json()
    assert current == saved.json()
    rows = await env.sql("SELECT case_id FROM current_therapy_review")
    assert str(rows[0][0]) == cid
    checked = await env.client.post(
        f"/api/v1/patients/{pid}/basket/check",
        headers=doctor,
        json={"new_items": [draft_item("new", sid, 50)], "stopped": []},
    )
    assert any(w["code"] == "duplicate_substance" for w in checked.json()["warnings"])
    removed = await env.client.put(url, headers=doctor, json={"base_token": current["base_token"], "items": []})
    assert removed.status_code == 200 and removed.json()["items"] == []


async def test_current_list_normalizes_label_and_drives_checks(env: Env) -> None:
    doctor, pid, patient, _ = await setup(env)
    url = f"/api/v1/patients/{pid}/therapy/current"
    initial = (await env.client.get(url, headers=doctor)).json()
    sid = label(env, "сертралин")
    body = {"base_token": initial["base_token"], "items": [{"label_id": sid, "substance": "wrong client text"}]}
    saved = await env.client.put(url, json=body, headers=doctor)
    assert saved.status_code == 200, saved.text
    current = saved.json()
    assert current["items"][0]["substance"] == "сертралин"
    assert current["base_token"] != initial["base_token"]
    assert (await env.client.put(url, json=body, headers=doctor)).status_code == 409
    assert (await env.client.put(url, json=body, headers=patient)).status_code == 403
    draft = {"new_items": [draft_item("new", sid, 50)], "stopped": []}
    report = (await env.client.post(f"/api/v1/patients/{pid}/basket/check", json=draft, headers=doctor)).json()
    assert any(w["code"] == "duplicate_substance" for w in report["warnings"])
    removed = await env.client.put(url, json={"base_token": current["base_token"], "items": []}, headers=doctor)
    assert removed.status_code == 200 and removed.json()["items"] == []
    report = (await env.client.post(f"/api/v1/patients/{pid}/basket/check", json=draft, headers=doctor)).json()
    assert not any(w["code"] == "duplicate_substance" for w in report["warnings"])
    assert len(await env.sql("SELECT id FROM current_therapy_review")) == 2
    assert len(await env.sql("SELECT id FROM audit_entry WHERE action='therapy.reconciled'")) == 2


async def test_mnn_only_reconciliation_preserves_prescription_history(env: Env) -> None:
    doctor, pid, _, _ = await setup(env)
    url = f"/api/v1/patients/{pid}/therapy/current"
    current = (await env.client.get(url, headers=doctor)).json()
    prescribed = await env.client.post(
        f"/api/v1/patients/{pid}/basket/confirm",
        headers=doctor,
        json={
            "base_token": current["base_token"],
            "draft": {"new_items": [draft_item("a", label(env, "сертралин"), 50)]},
        },
    )
    assert prescribed.status_code == 201, prescribed.text
    current = (await env.client.get(url, headers=doctor)).json()
    saved = await env.client.put(
        url,
        headers=doctor,
        json={
            "base_token": current["base_token"],
            "items": [{"ref": current["items"][0]["ref"], "substance": "АГОМЕЛАТИН"}],
        },
    )
    assert saved.status_code == 200, saved.text
    assert saved.json()["items"][0]["substance"] == "агомелатин"
    assert saved.json()["items"][0]["label_id"] is None
    history = (await env.client.get(f"/api/v1/patients/{pid}/prescriptions", headers=doctor)).json()
    assert history["items"][0]["items"] == prescribed.json()["items"]
    invalid = await env.client.put(
        url,
        headers=doctor,
        json={"base_token": saved.json()["base_token"], "items": [{"substance": "произвольное название"}]},
    )
    assert invalid.status_code == 422
    stranger = await env.doctor("other@kompas-demo.ru")
    assert (
        await env.client.put(url, headers=stranger, json={"base_token": saved.json()["base_token"], "items": []})
    ).status_code == 404


async def test_new_patient_report_is_not_lost_and_removed_report_stays_removed(env: Env) -> None:
    doctor, pid, patient, _ = await setup(env)
    url = f"/api/v1/patients/{pid}/therapy/current"
    await env.client.post(
        "/api/v1/me/drugs", headers=patient, json={"submission_id": new_id(), "substance": "сертралин"}
    )
    current = (await env.client.get(url, headers=doctor)).json()
    await env.client.put(url, headers=doctor, json={"base_token": current["base_token"], "items": []})
    await env.client.post(
        "/api/v1/me/drugs", headers=patient, json={"submission_id": new_id(), "substance": "агомелатин"}
    )
    current = (await env.client.get(url, headers=doctor)).json()
    assert [i["substance"] for i in current["items"]] == ["агомелатин"]
    confirmed = await env.client.post(
        f"/api/v1/patients/{pid}/basket/confirm",
        headers=doctor,
        json={
            "base_token": current["base_token"],
            "draft": {"new_items": [draft_item("a", label(env, "сертралин"), 50)]},
        },
    )
    assert confirmed.status_code == 201, confirmed.text
    current = (await env.client.get(url, headers=doctor)).json()
    assert len(current["items"]) == 2
