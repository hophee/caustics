"""Demo labs over the API: doctor-only entry, always flagged demo, used by the basket check."""

from tests.api.conftest import Env, new_id
from tests.api.test_diary_therapy import draft_item, label
from tests.api.test_gaps import setup


async def test_demo_labs_entry_and_basket_check(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    url = f"/api/v1/patients/{patient_id}/demo-labs"
    qtc = {"submission_id": new_id(), "code": "qtc", "value": 490, "measured_on": "2026-10-15"}
    r = await env.client.post(url, json=qtc, headers=doctor)
    assert r.status_code == 201, r.text
    assert r.json()["demo"] is True and r.json()["fresh"] is True and r.json()["unit"] == "мс"
    assert (await env.client.post(url, json=qtc, headers=doctor)).json()["lab_id"] == r.json()["lab_id"]
    bad = [
        {"code": "qtc", "value": 9000, "measured_on": "2026-10-15"},  # outside the plausible range
        {"code": "ferritin", "value": 10, "measured_on": "2026-10-15"},  # unknown test
        {"code": "qtc", "value": 440, "measured_on": "2026-11-15"},  # future
    ]
    for b in bad:
        resp = await env.client.post(url, json={"submission_id": new_id(), **b}, headers=doctor)
        assert resp.status_code == 422, b

    # doctor-only: the patient never sees results (D-109); another doctor can't see them either
    assert (await env.client.get(url, headers=patient)).status_code == 403
    other = await env.doctor("other@kompas-demo.ru")
    assert (await env.client.get(url, headers=other)).status_code == 404
    listed = (await env.client.get(url, headers=doctor)).json()
    assert listed["demo"] is True and [v["code"] for v in listed["values"]] == ["qtc"]

    draft = {"new_items": [draft_item("a", label(env, "амитриптилин"), 25)], "stopped": []}
    report = (await env.client.post(f"/api/v1/patients/{patient_id}/basket/check", json=draft, headers=doctor)).json()
    qt = next(w for w in report["warnings"] if w["code"] == "lab:qtc_prolonged")
    assert qt["severity"] == "contraindicated" and qt["sources"][0]["document"] == "демо-анализ"

    removed = await env.client.delete(f"{url}/{r.json()['lab_id']}", headers=doctor)
    assert removed.status_code == 200
    report = (await env.client.post(f"/api/v1/patients/{patient_id}/basket/check", json=draft, headers=doctor)).json()
    codes = {w["code"] for w in report["warnings"]}
    assert "lab:qtc_prolonged" not in codes and "lab_missing:qtc_prolonged" in codes
    assert (await env.client.delete(f"{url}/{r.json()['lab_id']}", headers=doctor)).status_code == 404
    actions = {a for (a,) in await env.sql("SELECT action FROM audit_entry WHERE entity_type = 'demo_lab_value'")}
    assert actions == {"lab.demo_added", "lab.demo_removed"}
