"""Endpoints: patient documents — patient uploads, doctor review, documents issued by the doctor, files."""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal
from urllib.parse import quote

from fastapi import APIRouter, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, Field

from kompas.api.deps import ActorDep, ClockDep, DoctorDep, KnowledgeDep, PatientDep, SessionDep
from kompas.modules.documents import service
from kompas.modules.documents.models import DocumentOrigin, DocumentStatus
from kompas.modules.documents.service import DocumentView, Upload
from kompas.modules.knowledge.bundle import Knowledge

router = APIRouter(tags=["documents"])


class FileOut(BaseModel):
    filename: str
    content_type: str
    size_bytes: int


class PatientDocumentOut(BaseModel):
    """The patient's view: no review note, no reviewer."""

    document_id: uuid.UUID
    origin: DocumentOrigin
    kind: str
    kind_label: str
    title: str
    document_date: date | None
    status: DocumentStatus
    created_at: datetime
    file: FileOut | None
    text: str | None
    content: dict[str, Any] | None  # prescription document: version snapshot (drugs, doses, dates)
    prescription_state: Literal["active", "expired"] | None

    @classmethod
    def of(cls, v: DocumentView, k: Knowledge) -> "PatientDocumentOut":
        d, f = v.document, v.file
        state = service.prescription_state(d, v.today)
        return cls(
            document_id=d.id,
            origin=d.origin,
            kind=d.kind,
            kind_label=k.document_rules.label(d.kind) or d.kind,
            title=d.title,
            document_date=d.document_date,
            status=d.status,
            created_at=d.created_at,
            file=FileOut(filename=f.filename, content_type=f.content_type, size_bytes=f.size_bytes) if f else None,
            text=d.text,
            content=d.content,
            prescription_state="active" if state == "active" else "expired" if state == "expired" else None,
        )


class DoctorDocumentOut(PatientDocumentOut):
    patient_id: uuid.UUID
    case_id: uuid.UUID | None
    review_note: str | None
    reviewed_at: datetime | None

    @classmethod
    def of(cls, v: DocumentView, k: Knowledge) -> "DoctorDocumentOut":
        d = v.document
        return cls(
            **PatientDocumentOut.of(v, k).model_dump(),
            patient_id=d.patient_id,
            case_id=d.case_id,
            review_note=d.review_note,
            reviewed_at=d.reviewed_at if d.origin is DocumentOrigin.PATIENT else None,
        )


class TodoOut(DoctorDocumentOut):
    patient_name: str


class ReviewIn(BaseModel):
    decision: Literal["confirmed", "rejected"]
    note: str | None = Field(default=None, max_length=2000)


class PrescriptionDocIn(BaseModel):
    submission_id: uuid.UUID
    prescription_id: uuid.UUID | None = None  # default: the latest confirmed version


async def _read(file: UploadFile, knowledge: Knowledge) -> Upload:
    """Read at most one byte over the limit, so an oversized upload is refused without loading it all."""
    data = await file.read(knowledge.document_rules.max_upload_bytes + 1)
    return Upload(filename=file.filename or "document", data=data)


@router.get("/documents/options")
async def get_options(knowledge: KnowledgeDep, _actor: ActorDep) -> dict[str, Any]:
    """Document kinds of each side and the upload limits (for the forms)."""
    rules = knowledge.document_rules
    return {
        "patient_kinds": [k.model_dump() for k in rules.patient_kinds],
        "doctor_kinds": [k.model_dump() for k in rules.doctor_kinds],
        "max_upload_mb": rules.max_upload_mb,
        "content_types": list(rules.content_types),
    }


@router.post("/me/documents", status_code=201)
async def post_upload(
    actor: PatientDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
    file: Annotated[UploadFile, File()],
    submission_id: Annotated[uuid.UUID, Form()],
    kind: Annotated[str, Form()],
    title: Annotated[str | None, Form(max_length=300)] = None,
    document_date: Annotated[date | None, Form()] = None,
) -> PatientDocumentOut:
    """POST /me/documents (multipart) — upload a PDF/JPEG/PNG; «не подтверждено» until the doctor reviews it."""
    document = await service.upload(
        session,
        actor,
        knowledge,
        submission_id=submission_id,
        kind=kind,
        title=title,
        document_date=document_date,
        file=await _read(file, knowledge),
        now=clock.now(),
    )
    return PatientDocumentOut.of(await service.view(session, document, clock.now()), knowledge)


@router.get("/me/documents")
async def get_my_documents(
    actor: PatientDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[PatientDocumentOut]:
    """GET /me/documents — own uploads and the documents from the doctor."""
    rows = await service.for_patient(session, actor, now=clock.now(), offset=offset, limit=limit)
    return [PatientDocumentOut.of(v, knowledge) for v in rows]


@router.post("/me/documents/{document_id}/remove")
async def post_remove(
    document_id: uuid.UUID, actor: PatientDep, session: SessionDep, knowledge: KnowledgeDep, clock: ClockDep
) -> PatientDocumentOut:
    """POST — withdraw an own upload the doctor has not reviewed yet."""
    document = await service.remove(session, actor, document_id, now=clock.now())
    return PatientDocumentOut.of(await service.view(session, document, clock.now()), knowledge)


@router.get("/documents/todo")
async def get_todo(actor: DoctorDep, session: SessionDep, knowledge: KnowledgeDep, clock: ClockDep) -> list[TodoOut]:
    """GET — uploads waiting for review across all the doctor's patients (oldest first)."""
    out = []
    for document, name in await service.doctor_todo(session, actor):
        v = await service.view(session, document, clock.now())
        out.append(TodoOut(**DoctorDocumentOut.of(v, knowledge).model_dump(), patient_name=name))
    return out


@router.get("/documents/{document_id}/file")
async def get_file(document_id: uuid.UUID, actor: ActorDep, session: SessionDep) -> Response:
    """GET — the file, for the document's patient or the treating doctor. Served sandboxed (no scripts)."""
    stored = await service.get_file(session, actor, document_id)
    return Response(
        content=stored.data,
        media_type=stored.content_type,
        headers={
            "Content-Disposition": f"inline; filename*=UTF-8''{quote(stored.filename)}",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox",
            "Cache-Control": "private, no-store",
        },
    )


@router.get("/patients/{patient_id}/documents")
async def get_documents(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
) -> list[DoctorDocumentOut]:
    """GET — the patient's documents for the doctor: unreviewed uploads first."""
    rows = await service.for_doctor(session, actor, patient_id, now=clock.now(), offset=offset, limit=limit)
    return [DoctorDocumentOut.of(v, knowledge) for v in rows]


@router.post("/patients/{patient_id}/documents", status_code=201)
async def post_issue(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
    submission_id: Annotated[uuid.UUID, Form()],
    kind: Annotated[str, Form()],
    title: Annotated[str | None, Form(max_length=300)] = None,
    text: Annotated[str | None, Form(max_length=20000)] = None,
    document_date: Annotated[date | None, Form()] = None,
    file: Annotated[UploadFile | None, File()] = None,
) -> DoctorDocumentOut:
    """POST (multipart) — issue a visit summary or выписка to the patient: text and/or a file."""
    document = await service.issue(
        session,
        actor,
        knowledge,
        patient_id,
        submission_id=submission_id,
        kind=kind,
        title=title,
        text=text,
        document_date=document_date,
        file=await _read(file, knowledge) if file is not None else None,
        now=clock.now(),
    )
    return DoctorDocumentOut.of(await service.view(session, document, clock.now()), knowledge)


@router.post("/patients/{patient_id}/documents/prescription", status_code=201)
async def post_prescription_document(
    patient_id: uuid.UUID,
    body: PrescriptionDocIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> DoctorDocumentOut:
    """POST — issue a prescription document from a confirmed version (printable on the website)."""
    document = await service.issue_prescription(
        session, actor, knowledge, patient_id, **body.model_dump(), now=clock.now()
    )
    return DoctorDocumentOut.of(await service.view(session, document, clock.now()), knowledge)


@router.post("/documents/{document_id}/review")
async def post_review(
    document_id: uuid.UUID,
    body: ReviewIn,
    actor: DoctorDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
) -> DoctorDocumentOut:
    """POST — confirm or reject (reason required, doctor-only) a patient upload."""
    document = await service.review(
        session, actor, document_id, decision=DocumentStatus(body.decision), note=body.note, now=clock.now()
    )
    return DoctorDocumentOut.of(await service.view(session, document, clock.now()), knowledge)
