"""Documents over the API: patient upload → «не подтверждено» → doctor review; doctor-issued documents;
prescription document from a confirmed version; file access control."""

from datetime import timedelta

from tests.api.conftest import Env, new_id, walk
from tests.api.test_gaps import prescribe, relogin, setup

PDF = b"%PDF-1.4\n%synthetic test document\n%%EOF\n"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


async def upload(
    env: Env, headers: dict[str, str], data: bytes = PDF, name: str = "выписка.pdf", **fields: str
) -> object:
    form = {"submission_id": new_id(), "kind": "outside_record", **fields}
    return await env.client.post(
        "/api/v1/me/documents", data=form, files={"file": (name, data, "application/pdf")}, headers=headers
    )


async def test_patient_upload_review_and_access(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    submission = new_id()
    form = {"submission_id": submission, "kind": "outside_record", "title": "Выписка из стационара"}
    files = {"file": ("vypiska.pdf", PDF, "application/pdf")}
    r = await env.client.post("/api/v1/me/documents", data=form, files=files, headers=patient)
    assert r.status_code == 201, r.text
    doc = r.json()
    assert doc["status"] == "unconfirmed" and doc["file"]["content_type"] == "application/pdf"
    retry = await env.client.post("/api/v1/me/documents", data=form, files=files, headers=patient)
    assert retry.json()["document_id"] == doc["document_id"]
    kinds = [n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=doctor)).json()]
    assert kinds.count("document_uploaded") == 1
    todo = (await env.client.get("/api/v1/documents/todo", headers=doctor)).json()
    assert [t["document_id"] for t in todo] == [doc["document_id"]] and todo[0]["patient_name"] == "Тестовая Анна"

    # the type comes from the file's signature, not from its name or declared type
    fake = await upload(env, patient, b"MZ\x90\x00 not a pdf", "scan.pdf")
    assert fake.status_code == 422
    big = await upload(env, patient, PDF + b"0" * (10 * 1024 * 1024))
    assert big.status_code == 422
    assert (await upload(env, patient, kind="prescription")).status_code == 422  # doctor-only kind
    assert (await upload(env, patient, document_date="2026-12-01")).status_code == 422
    photo = await upload(env, patient, PNG, "IMG_001.png", kind="lab_report")
    assert photo.json()["file"]["content_type"] == "image/png" and photo.json()["title"] == "Результаты анализов"

    # the file: the patient and the treating doctor only
    url = f"/api/v1/documents/{doc['document_id']}/file"
    got = await env.client.get(url, headers=patient)
    assert got.status_code == 200 and got.content == PDF
    assert got.headers["content-type"] == "application/pdf" and got.headers["content-security-policy"] == "sandbox"
    assert (await env.client.get(url, headers=doctor)).content == PDF
    other_doctor = await env.doctor("other@kompas-demo.ru")
    _, stranger = await env.patient(other_doctor, "p2@kompas-demo.ru")
    assert (await env.client.get(url, headers=other_doctor)).status_code == 404
    assert (await env.client.get(url, headers=stranger)).status_code == 404
    review_url = f"/api/v1/documents/{doc['document_id']}/review"
    assert (await env.client.post(review_url, json={"decision": "confirmed"}, headers=other_doctor)).status_code == 404

    # review: a rejection needs a reason, which stays doctor-only
    no_reason = await env.client.post(review_url, json={"decision": "rejected"}, headers=doctor)
    assert no_reason.status_code == 422
    rejected = await env.client.post(
        review_url, json={"decision": "rejected", "note": "Чужая фамилия в документе"}, headers=doctor
    )
    assert rejected.json()["status"] == "rejected" and rejected.json()["review_note"] == "Чужая фамилия в документе"
    mine = (await env.client.get("/api/v1/me/documents", headers=patient)).json()
    assert {m["status"] for m in mine} == {"rejected", "unconfirmed"}
    assert not {k for k, _ in walk(mine)} & {"review_note", "reviewed_at", "patient_id"}
    assert (
        await env.client.post(f"/api/v1/me/documents/{doc['document_id']}/remove", headers=patient)
    ).status_code == 409
    withdrawn = await env.client.post(f"/api/v1/me/documents/{photo.json()['document_id']}/remove", headers=patient)
    assert withdrawn.status_code == 200
    assert len((await env.client.get("/api/v1/me/documents", headers=patient)).json()) == 1
    assert (await env.client.get("/api/v1/documents/todo", headers=doctor)).json() == []
    actions = {a for (a,) in await env.sql("SELECT action FROM audit_entry WHERE entity_type = 'document'")}
    assert actions == {"document.uploaded", "document.reviewed", "document.removed"}
    assert patient_id


async def test_doctor_documents_and_prescription_document(env: Env) -> None:
    doctor, patient_id, patient, _ = await setup(env)
    url = f"/api/v1/patients/{patient_id}/documents"
    summary = await env.client.post(
        url,
        data={"submission_id": new_id(), "kind": "visit_summary", "text": "Продолжаем терапию, дневник ежедневно"},
        headers=doctor,
    )
    assert summary.status_code == 201, summary.text
    assert summary.json()["status"] == "issued" and summary.json()["title"] == "Итоги приёма"
    with_file = await env.client.post(
        url,
        data={"submission_id": new_id(), "kind": "discharge_summary", "title": "Выписка"},
        files={"file": ("vypiska.pdf", PDF, "application/pdf")},
        headers=doctor,
    )
    assert with_file.status_code == 201 and with_file.json()["file"]["filename"] == "vypiska.pdf"
    empty = await env.client.post(url, data={"submission_id": new_id(), "kind": "visit_summary"}, headers=doctor)
    assert empty.status_code == 422
    wrong = await env.client.post(
        url, data={"submission_id": new_id(), "kind": "prescription", "text": "x"}, headers=doctor
    )
    assert wrong.status_code == 422
    forbidden = await env.client.post(
        url, data={"submission_id": new_id(), "kind": "visit_summary", "text": "x"}, headers=patient
    )
    assert forbidden.status_code == 403
    no_rx = await env.client.post(f"{url}/prescription", json={"submission_id": new_id()}, headers=doctor)
    assert no_rx.status_code == 404

    await prescribe(env, doctor, patient_id, "08:00", "20:00")
    rx = await env.client.post(f"{url}/prescription", json={"submission_id": new_id()}, headers=doctor)
    assert rx.status_code == 201, rx.text
    content = rx.json()["content"]
    assert rx.json()["prescription_state"] == "active" and content["valid_until"] == "2026-11-15"
    assert [i["at"] for i in content["items"][0]["intakes"]] == ["08:00", "20:00"]
    assert content["doctor_name"] == "Доктор Тест"

    mine = (await env.client.get("/api/v1/me/documents", headers=patient)).json()
    assert {m["kind"] for m in mine} == {"visit_summary", "discharge_summary", "prescription"}
    assert not {k for k, _ in walk(mine)} & {"warnings", "justifications", "provenance", "review_note"}
    kinds = [n["kind"] for n in (await env.client.get("/api/v1/me/notifications", headers=patient)).json()]
    assert kinds.count("document_issued") == 3
    review = await env.client.post(
        f"/api/v1/documents/{summary.json()['document_id']}/review", json={"decision": "confirmed"}, headers=doctor
    )
    assert review.status_code == 409  # only patient uploads are reviewed

    env.clock.advance(timedelta(days=30))
    doctor, patient = await relogin(env)
    later = (await env.client.get("/api/v1/me/documents", headers=patient)).json()
    assert next(m for m in later if m["kind"] == "prescription")["prescription_state"] == "expired"
