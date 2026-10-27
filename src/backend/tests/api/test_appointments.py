"""Planned visits over the API: doctor plans → patient sees → reminders → patient request → doctor decides."""

from datetime import timedelta

from kompas.modules.appointments.service import send_due_reminders
from tests.api.conftest import Env, new_id, walk
from tests.api.test_gaps import relogin, setup

# NOW = 2026-10-19 09:00 UTC (12:00 Moscow)


def plan(starts_at: str = "2026-10-20T12:00:00+03:00", **fields: object) -> dict[str, object]:
    return {"submission_id": new_id(), "starts_at": starts_at, "place": "кабинет 214", **fields}


async def kinds(env: Env, headers: dict[str, str]) -> list[str]:
    return [n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=headers)).json()]


async def remind(env: Env) -> int:
    return await send_due_reminders(env.factory, env.knowledge, now=env.clock.now())


async def test_plan_view_and_access(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    body = plan(purpose="оценка после сигнала", note_for_patient="Возьмите выписку")
    r = await env.client.post(f"/api/v1/patients/{patient_id}/appointments", json=body, headers=doctor)
    assert r.status_code == 201, r.text
    retry = await env.client.post(f"/api/v1/patients/{patient_id}/appointments", json=body, headers=doctor)
    assert retry.json()["appointment_id"] == r.json()["appointment_id"]
    assert r.json()["purpose"] == "оценка после сигнала" and r.json()["duration_minutes"] == 30

    mine = (await env.client.get("/api/v1/me/appointments?scope=upcoming", headers=patient)).json()
    assert len(mine) == 1 and mine[0]["doctor_name"] == "Доктор Тест" and mine[0]["place"] == "кабинет 214"
    assert not {k for k, _ in walk(mine)} & {"purpose", "cancel_reason", "visit_id", "patient_id"}
    assert "appointment_planned" in await kinds(env, patient)

    bad = [
        plan(starts_at="2026-10-18T12:00:00+03:00"),  # in the past
        plan(place=None),  # in person without a place
        plan(mode="online", place=None, link="http://insecure.example"),
        plan(starts_at="2026-10-20T12:00:00"),  # no time zone
    ]
    for b in bad:
        resp = await env.client.post(f"/api/v1/patients/{patient_id}/appointments", json=b, headers=doctor)
        assert resp.status_code == 422, (b, resp.text)
    online = plan(mode="online", place=None, link="https://meet.example/abc")
    assert (
        await env.client.post(f"/api/v1/patients/{patient_id}/appointments", json=online, headers=doctor)
    ).status_code == 201

    other = await env.doctor("other@kompas-demo.ru")
    assert (await env.client.get(f"/api/v1/patients/{patient_id}/appointments", headers=other)).status_code == 404
    assert (
        await env.client.post(f"/api/v1/patients/{patient_id}/appointments", json=plan(), headers=patient)
    ).status_code == 403
    appointment_id = r.json()["appointment_id"]
    assert (
        await env.client.post(f"/api/v1/appointments/{appointment_id}/cancel", json={}, headers=other)
    ).status_code == 404
    schedule = (await env.client.get("/api/v1/appointments/schedule", headers=doctor)).json()
    assert [s["patient_name"] for s in schedule] == ["Тестовая Анна", "Тестовая Анна"]


async def test_reminders_day_and_two_hours_before_follow_moves_and_cancel(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    r = await env.client.post(
        f"/api/v1/patients/{patient_id}/appointments", json=plan("2026-10-20T15:00:00+03:00"), headers=doctor
    )
    appointment_id = r.json()["appointment_id"]
    assert await remind(env) == 0  # 27 h before
    env.clock.advance(timedelta(hours=3))  # 2026-10-19 15:00 Moscow: exactly a day before
    assert await remind(env) == 1
    assert await remind(env) == 0  # never twice
    env.clock.advance(timedelta(hours=22, minutes=10))  # 13:10 Moscow on the day: 1 h 50 min before
    assert await remind(env) == 1
    doctor, patient = await relogin(env)
    reminders = (await env.client.get("/api/v1/me/notifications", headers=patient)).json()
    sent = sorted(n["payload"]["reminder"] for n in reminders if n["kind"] == "appointment_reminder")
    assert sent == ["day", "soon"]

    # moved to tomorrow 13:30 → the reminders start again for the new time
    moved = await env.client.patch(
        f"/api/v1/appointments/{appointment_id}", json={"starts_at": "2026-10-21T13:30:00+03:00"}, headers=doctor
    )
    assert moved.status_code == 200, moved.text
    assert "appointment_changed" in await kinds(env, patient)
    env.clock.advance(timedelta(minutes=20))  # 2026-10-20 13:30 Moscow: a day before the new time
    assert await remind(env) == 1

    # cancelled → the remaining «soon» reminder is never sent
    doctor, patient = await relogin(env)
    await env.client.post(f"/api/v1/appointments/{appointment_id}/cancel", json={}, headers=doctor)
    env.clock.advance(timedelta(hours=22))  # 2026-10-21 11:30 Moscow: 2 h before
    assert await remind(env) == 0


async def test_moved_visit_gets_fresh_reminder(env: Env) -> None:
    doctor, patient_id, _, _ = await setup(env)
    r = await env.client.post(
        f"/api/v1/patients/{patient_id}/appointments", json=plan("2026-10-19T14:00:00+03:00"), headers=doctor
    )
    env.clock.advance(timedelta(minutes=5))  # 12:05 Moscow, 1 h 55 min before
    assert await remind(env) == 1
    await env.client.patch(
        f"/api/v1/appointments/{r.json()['appointment_id']}",
        json={"starts_at": "2026-10-19T14:20:00+03:00"},
        headers=doctor,
    )
    env.clock.advance(timedelta(minutes=20))  # 12:25, 1 h 55 min before the new time
    assert await remind(env) == 1
    assert await remind(env) == 0


async def test_patient_request_and_doctor_decision(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    r = await env.client.post(f"/api/v1/patients/{patient_id}/appointments", json=plan(), headers=doctor)
    appointment_id = r.json()["appointment_id"]
    url = f"/api/v1/me/appointments/{appointment_id}/change-requests"
    body = {
        "submission_id": new_id(),
        "kind": "reschedule",
        "proposed_starts_at": "2026-10-22T10:00:00+03:00",
        "reason": "командировка",
    }
    first = await env.client.post(url, json=body, headers=patient)
    assert first.status_code == 201, first.text
    assert (await env.client.post(url, json=body, headers=patient)).json()["request_id"] == first.json()["request_id"]
    again = await env.client.post(url, json={**body, "submission_id": new_id()}, headers=patient)
    assert again.status_code == 409  # one open request per visit
    assert "appointment_request" in await kinds(env, doctor)
    pending = (await env.client.get("/api/v1/appointments/requests", headers=doctor)).json()
    assert [p["request_id"] for p in pending] == [first.json()["request_id"]]
    # nothing changed yet
    mine = (await env.client.get("/api/v1/me/appointments", headers=patient)).json()[0]
    assert mine["starts_at"].startswith("2026-10-20T09:00") and mine["request"]["status"] == "pending"

    decided = await env.client.post(
        f"/api/v1/appointment-requests/{first.json()['request_id']}/decision",
        json={"accept": True, "starts_at": "2026-10-22T11:00:00+03:00", "reply": "Жду в 11:00"},
        headers=doctor,
    )
    assert decided.status_code == 200, decided.text
    mine = (await env.client.get("/api/v1/me/appointments", headers=patient)).json()[0]
    assert mine["starts_at"].startswith("2026-10-22T08:00") and mine["request"]["status"] == "accepted"
    assert mine["request"]["reply"] == "Жду в 11:00"
    assert "appointment_request_decided" in await kinds(env, patient)
    twice = await env.client.post(
        f"/api/v1/appointment-requests/{first.json()['request_id']}/decision", json={"accept": False}, headers=doctor
    )
    assert twice.status_code == 409

    cancel = await env.client.post(url, json={"submission_id": new_id(), "kind": "cancel"}, headers=patient)
    declined = await env.client.post(
        f"/api/v1/appointment-requests/{cancel.json()['request_id']}/decision", json={"accept": False}, headers=doctor
    )
    assert declined.json()["status"] == "planned"
    cancel = await env.client.post(url, json={"submission_id": new_id(), "kind": "cancel"}, headers=patient)
    accepted = await env.client.post(
        f"/api/v1/appointment-requests/{cancel.json()['request_id']}/decision", json={"accept": True}, headers=doctor
    )
    assert accepted.json()["status"] == "cancelled"
    assert (await env.client.get("/api/v1/me/appointments?scope=upcoming", headers=patient)).json() == []

    # too close to the start: the patient is told to contact the clinic
    soon = await env.client.post(
        f"/api/v1/patients/{patient_id}/appointments", json=plan("2026-10-19T13:30:00+03:00"), headers=doctor
    )
    late = await env.client.post(
        f"/api/v1/me/appointments/{soon.json()['appointment_id']}/change-requests",
        json={"submission_id": new_id(), "kind": "cancel"},
        headers=patient,
    )
    assert late.status_code == 409
    other_doctor = await env.doctor("other@kompas-demo.ru")
    _, stranger = await env.patient(other_doctor, "p2@kompas-demo.ru")
    foreign = await env.client.post(url, json={"submission_id": new_id(), "kind": "cancel"}, headers=stranger)
    assert foreign.status_code == 404


async def test_complete_with_visit_and_patient_summary(env: Env) -> None:
    doctor, patient_id, patient, case_id = await setup(env)
    r = await env.client.post(
        f"/api/v1/patients/{patient_id}/appointments", json=plan("2026-10-19T14:00:00+03:00"), headers=doctor
    )
    appointment_id = r.json()["appointment_id"]
    early = await env.client.post(
        f"/api/v1/patients/{patient_id}/appointments", json=plan("2026-10-25T14:00:00+03:00"), headers=doctor
    )
    too_early = await env.client.post(
        f"/api/v1/appointments/{early.json()['appointment_id']}/complete", json={}, headers=doctor
    )
    assert too_early.status_code == 409
    visit = await env.client.post(
        f"/api/v1/cases/{case_id}/visits",
        json={"kind": "repeat", "held_at": "2026-10-19T11:00:00Z", "content": {"complaints": "лучше"}},
        headers=doctor,
    )
    assert visit.status_code == 201, visit.text
    done = await env.client.post(
        f"/api/v1/appointments/{appointment_id}/complete",
        json={"visit_id": visit.json()["visit_id"], "patient_summary": "Продолжаем терапию, дневник ежедневно"},
        headers=doctor,
    )
    assert done.status_code == 200, done.text
    assert done.json()["status"] == "completed" and done.json()["visit_id"] == visit.json()["visit_id"]
    past = (await env.client.get("/api/v1/me/appointments?scope=past", headers=patient)).json()
    assert past[0]["patient_summary"] == "Продолжаем терапию, дневник ежедневно"
    assert "appointment_summary" in await kinds(env, patient)
    edit = await env.client.patch(f"/api/v1/appointments/{appointment_id}", json={"place": "214"}, headers=doctor)
    assert edit.status_code == 409
    actions = {a for (a,) in await env.sql("SELECT action FROM audit_entry WHERE entity_type = 'appointment'")}
    assert {"appointment.planned", "appointment.completed"} <= actions
