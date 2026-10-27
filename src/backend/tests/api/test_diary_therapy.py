"""Diary triage and the basket over the API, plus outbox and notification plumbing."""

from datetime import timedelta
from typing import Any

from kompas.core import outbox
from kompas.core.db import transaction
from kompas.worker import tick
from tests.api.conftest import Env, new_id, walk


async def setup(env: Env) -> tuple[dict[str, str], str, dict[str, str], str]:
    doctor = await env.doctor("doc@kompas-demo.ru")
    patient_id, patient = await env.patient(doctor, "p@kompas-demo.ru")
    case_id = await env.case(doctor, patient_id)
    return doctor, patient_id, patient, case_id


def entry(**fields: Any) -> dict[str, Any]:
    base = {
        "submission_id": new_id(),
        "day": "2026-10-19",
        "mood": 5,
        "anxiety": 4,
        "energy": 5,
        "sleep": "normal",
        "complaints": [],
        "side_effects": [],
        "self_harm_thoughts": False,
        "text": None,
    }
    return {**base, **fields}


# --------------------------------------------------------------------------- diary


async def test_diary_self_harm_is_urgent_for_doctor_and_invisible_to_patient(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    r = await env.client.post("/api/v1/me/diary", json=entry(self_harm_thoughts=True), headers=patient)
    assert r.status_code == 201, r.text
    keys = {k for k, _ in walk(r.json())} | {
        k for k, _ in walk((await env.client.get("/api/v1/me/diary", headers=patient)).json())
    }
    assert not keys & {"triage", "notes", "level", "reasons"}
    doc = (await env.client.get(f"/api/v1/patients/{patient_id}/diary", headers=doctor)).json()
    assert doc[0]["triage"]["level"] == "urgent" and doc[0]["triage"]["label"] == "Срочно"
    alerts = (await env.client.get("/api/v1/alerts", headers=doctor)).json()
    assert alerts["total"] == 1 and alerts["items"][0]["source"] == "diary"
    assert alerts["items"][0]["scenario"] == "danger_now"


async def test_diary_revisions_keep_history_and_model_runs_in_worker(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    first = await env.client.post("/api/v1/me/diary", json=entry(text="Всё спокойно"), headers=patient)
    second = await env.client.post("/api/v1/me/diary", json=entry(text="Не хочу больше жить"), headers=patient)
    assert first.json()["entry_id"] == second.json()["entry_id"]
    assert (first.json()["revision"], second.json()["revision"]) == (1, 2)
    assert len(await env.sql("SELECT id FROM diary_entry_revision")) == 2
    doc = (await env.client.get(f"/api/v1/patients/{patient_id}/diary", headers=doctor)).json()
    assert len(doc) == 1 and doc[0]["text"] == "Не хочу больше жить" and doc[0]["triage"]["model_pending"]
    await env.run_worker()
    doc = (await env.client.get(f"/api/v1/patients/{patient_id}/diary", headers=doctor)).json()
    triage = doc[0]["triage"]
    assert triage["model_pending"] is False and triage["model_name"]
    stale = await env.sql(
        "SELECT model_pending FROM diary_triage t JOIN diary_entry_revision r ON r.id = t.revision_id"
        " WHERE r.revision = 1"
    )
    assert stale == [(False,)]


async def test_diary_rejects_future_day_and_repeats_by_submission(env: Env) -> None:
    _, _, patient, _ = await setup(env)
    future = await env.client.post("/api/v1/me/diary", json=entry(day="2026-10-21"), headers=patient)
    assert future.status_code == 422
    body = entry()
    a = await env.client.post("/api/v1/me/diary", json=body, headers=patient)
    b = await env.client.post("/api/v1/me/diary", json=body, headers=patient)
    assert a.json()["revision_id"] == b.json()["revision_id"]


async def test_doctor_notes_and_freshness(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    saved = (await env.client.post("/api/v1/me/diary", json=entry(), headers=patient)).json()
    r = await env.client.post(f"/api/v1/diary/{saved['entry_id']}/notes", json={"text": "Обсудить сон"}, headers=doctor)
    assert r.status_code == 201
    assert (
        await env.client.post(f"/api/v1/diary/{saved['entry_id']}/notes", json={"text": "x"}, headers=patient)
    ).status_code == 403
    doc = (await env.client.get(f"/api/v1/patients/{patient_id}/diary", headers=doctor)).json()
    assert [n["text"] for n in doc[0]["notes"]] == ["Обсудить сон"]
    mine = (await env.client.get("/api/v1/me/diary", headers=patient)).json()
    assert "notes" not in mine[0]
    fresh = (await env.client.get(f"/api/v1/patients/{patient_id}/freshness", headers=doctor)).json()
    assert fresh["last_entry_on"] == "2026-10-19" and fresh["missed_days"] == 0


# --------------------------------------------------------------------------- therapy


def label(env: Env, substance: str) -> str:
    assert env.knowledge.drugs is not None
    return env.knowledge.drugs.by_substance(substance)[0].id


def draft_item(key: str, label_id: str, mg: int, origin: dict[str, str] | None = None) -> dict[str, Any]:
    return {
        "key": key,
        "label_id": label_id,
        "origin": origin or {"kind": "search"},
        "regimen": {
            "intakes": [{"at": "08:00", "amount": mg, "unit": "мг"}],
            "start": "2026-10-20",
            "duration_days": 28,
        },
    }


async def test_basket_check_confirm_justify_and_patient_view(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    profile = {
        "birth_date": "1992-03-01",
        "sex": "female",
        "pregnant": "no",
        "lactating": "yes",
        "diagnoses": [],
        "allergies": [],
        "intolerant_excipients": [],
        "past_intolerance": [],
        "past_inefficacy": [],
    }
    assert (
        await env.client.put(f"/api/v1/patients/{patient_id}/profile", json=profile, headers=doctor)
    ).status_code == 200
    current = (await env.client.get(f"/api/v1/patients/{patient_id}/therapy/current", headers=doctor)).json()
    assert current["items"] == []
    draft = {"new_items": [draft_item("a", label(env, "амитриптилин"), 300)], "stopped": []}
    report = (await env.client.post(f"/api/v1/patients/{patient_id}/basket/check", json=draft, headers=doctor)).json()
    severities = {b["severity"] for b in report["boxes"]}
    assert "contraindicated" in severities
    body = {"draft": draft, "base_token": current["base_token"], "justifications": {}}
    refused = await env.client.post(f"/api/v1/patients/{patient_id}/basket/confirm", json=body, headers=doctor)
    assert refused.status_code == 422
    needed = [p["pointer"] for p in refused.json()["error"]["details"] if p["code"] == "justification_required"]
    assert needed
    body["justifications"] = {wid: "Решение консилиума, грудное вскармливание прекращено" for wid in needed}
    r = await env.client.post(f"/api/v1/patients/{patient_id}/basket/confirm", json=body, headers=doctor)
    assert r.status_code == 201, r.text
    prescription = r.json()
    assert prescription["version"] == 1 and prescription["items"][0]["provenance"]["kr_status"]
    # stale base token after the confirmation
    stale = await env.client.post(f"/api/v1/patients/{patient_id}/basket/confirm", json=body, headers=doctor)
    assert stale.status_code == 409
    lines = (await env.client.get("/api/v1/me/prescription", headers=patient)).json()
    assert lines[0]["intakes"] == [{"at": "08:00", "amount_mg": 300.0}]
    assert not {k for k, _ in walk(lines)} & {"warnings", "provenance", "suggested_dose_text", "justifications"}
    await env.run_worker()
    assert "prescription" in [
        n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=patient)).json()
    ]
    audit = await env.sql("SELECT details FROM audit_entry WHERE action = 'prescription.confirmed'")
    assert audit[0][0]["justified"] and audit[0][0]["justified"][0]["warning_id"] in needed


async def test_courses_survive_versions_and_reported_drugs_invalidate_the_base(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    current = (await env.client.get(f"/api/v1/patients/{patient_id}/therapy/current", headers=doctor)).json()
    draft = {"new_items": [draft_item("a", label(env, "сертралин"), 50, {"kind": "kr_suggestion", "row_id": "kr-098"})]}
    body = {"draft": draft, "base_token": current["base_token"]}
    v1 = await env.client.post(f"/api/v1/patients/{patient_id}/basket/confirm", json=body, headers=doctor)
    assert v1.status_code == 201, v1.text
    course = v1.json()["items"][0]["course_id"]
    # the patient reports another drug: the doctor's old view is now stale
    current = (await env.client.get(f"/api/v1/patients/{patient_id}/therapy/current", headers=doctor)).json()
    reported = {"submission_id": new_id(), "substance": "мелатонин"}
    assert (await env.client.post("/api/v1/me/drugs", json=reported, headers=patient)).status_code == 201
    second = {
        "draft": {"new_items": [draft_item("b", label(env, "агомелатин"), 25)]},
        "base_token": current["base_token"],
    }
    assert (
        await env.client.post(f"/api/v1/patients/{patient_id}/basket/confirm", json=second, headers=doctor)
    ).status_code == 409
    current = (await env.client.get(f"/api/v1/patients/{patient_id}/therapy/current", headers=doctor)).json()
    refs = [i["ref"] for i in current["items"]]
    assert course in refs and any(r.startswith("reported:") for r in refs)
    reported_ref = next(r for r in refs if r.startswith("reported:"))
    second = {"draft": {"new_items": [], "stopped": [reported_ref]}, "base_token": current["base_token"]}
    v2 = await env.client.post(f"/api/v1/patients/{patient_id}/basket/confirm", json=second, headers=doctor)
    assert v2.status_code == 201, v2.text
    assert [i["course_id"] for i in v2.json()["items"]] == [course]
    history = (await env.client.get(f"/api/v1/patients/{patient_id}/prescriptions", headers=doctor)).json()
    assert [p["version"] for p in history["items"]] == [2, 1]
    kinds = [n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=doctor)).json()]
    assert "reported_drug" in kinds


async def test_suggestions_and_drug_search(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    named = (await env.client.get("/api/v1/guidelines/diagnosis", params={"diagnosis": "F33.2"}, headers=doctor)).json()
    assert named["name"] and "stages" not in named
    r = await env.client.get(f"/api/v1/patients/{patient_id}/suggestions", headers=doctor)
    assert r.status_code == 200 and len({row["stage"] for row in r.json()["rows"]}) > 1
    found = (await env.client.get("/api/v1/drugs", params={"q": "сертралин"}, headers=patient)).json()
    assert found["total"] >= 1
    browse = (await env.client.get("/api/v1/drugs", params={"limit": 2}, headers=doctor)).json()
    assert len(browse["items"]) == 2 and browse["total"] > found["total"]
    forms = await env.client.get("/api/v1/drugs/forms", headers=doctor)
    assert forms.status_code == 200 and forms.json()
    first = found["items"][0]
    filtered = await env.client.get(
        "/api/v1/drugs",
        params={"substance": "сертралин", "trade_name": first["trade_name"], "forms": first["form"]},
        headers=doctor,
    )
    assert filtered.status_code == 200 and filtered.json()["total"] >= 1
    assert all(row["form"] == first["form"] for row in filtered.json()["items"])
    no_forms = await env.client.get("/api/v1/drugs", params={"forms": "__none__"}, headers=doctor)
    assert no_forms.json()["total"] == 0
    vocabulary = (await env.client.get("/api/v1/drugs/vocabulary", headers=doctor)).json()
    assert "сертралин" in vocabulary["substances"] and vocabulary["excipients"]
    assert all(t["substances"] for t in vocabulary["trade_names"])
    assert (await env.client.get("/api/v1/drugs/vocabulary", headers=patient)).status_code == 403
    form = (await env.client.get("/api/v1/scales/PHQ-9", headers=patient)).json()
    assert len(form["items"]) == 9 and "bands" not in form and "critical" not in form
    assert (await env.client.get("/api/v1/scales/HDRS-17", headers=patient)).status_code == 404


# --------------------------------------------------------------------------- plumbing


async def test_guideline_diagnoses_list_existing_sources_only(env: Env) -> None:
    doctor, _, patient, _ = await setup(env)
    r = await env.client.get("/api/v1/guidelines/diagnoses", headers=doctor)
    assert r.status_code == 200
    by_code = {d["code"]: d for d in r.json()}
    assert len(by_code) == 21
    f32, f33 = by_code["F32.1"], by_code["F33.1"]
    assert [g["id"] for g in f32["guidelines"]] == ["301_3"] and f32["standards"] == []
    assert f33["guidelines"][0]["url"] == "https://cr.minzdrav.gov.ru/preview-cr/301_3"
    assert [(s["number"], s["date"]) for s in f33["standards"]] == [("745н", "2026-08-24")]
    assert (await env.client.get("/api/v1/guidelines/diagnoses", headers=patient)).status_code == 403


async def test_outbox_retries_then_dead_letters(env: Env) -> None:
    async with transaction(env.factory) as session:
        await outbox.enqueue(session, "test.boom", {"x": 1}, now=env.clock.now(), max_attempts=3)

    async def boom(_factory: Any, _payload: Any) -> None:
        raise RuntimeError("handler failed")

    table = {"test.boom": boom}
    statuses = []
    for _ in range(3):
        await tick(env.factory, table, env.settings, env.clock, topics=["test.boom"])
        statuses.append((await env.sql("SELECT status, attempts FROM outbox_message"))[0])
        env.clock.advance(timedelta(hours=1))
    assert statuses == [("failed", 1), ("failed", 2), ("dead", 3)]
    assert "handler failed" in (await env.sql("SELECT last_error FROM outbox_message"))[0][0]


async def test_stream_ticket_is_single_use(env: Env) -> None:
    doctor = await env.doctor("doc@kompas-demo.ru")
    ticket = (await env.client.post("/api/v1/me/notifications/ticket", headers=doctor)).json()["ticket"]
    from kompas.modules.notifications.service import consume_ticket

    async with transaction(env.factory) as session:
        await consume_ticket(session, ticket, secret=env.settings.invite_secret.get_secret_value(), now=env.clock.now())
    r = await env.client.get("/api/v1/me/notifications/stream", params={"ticket": ticket})
    assert r.status_code == 401
