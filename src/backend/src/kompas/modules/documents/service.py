"""Patient documents (spec §11 «Документы пациента», T2).

Uploads from the patient are «не подтверждено» until the treating doctor reviews them; the doctor's
documents (visit summary, выписка, prescription) are issued to the patient. File types are checked by
their signature (PDF, JPEG, PNG), never by the name or the declared type. Nothing is extracted from the
files automatically — the honest MVP is "upload + doctor review" (T2 stays uncovered).
"""

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import case, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import undefer

from kompas.core.actor import Actor
from kompas.core.clock import local_date
from kompas.core.errors import InvalidInputError, InvalidStateError, NotFoundError, PermissionDeniedError, Problem
from kompas.modules.audit import service as audit
from kompas.modules.cases.models import CaseStatus, MedicalCase
from kompas.modules.cases.service import patient_timezone, require_treating_doctor
from kompas.modules.documents.models import Document, DocumentFile, DocumentOrigin, DocumentStatus
from kompas.modules.identity.models import Doctor, Patient
from kompas.modules.identity.service import ensure_patient_access
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.notifications import service as notifications
from kompas.modules.therapy.models import Prescription
from kompas.modules.therapy.service import prescription_lines

_SIGNATURES = (
    (b"%PDF-", "application/pdf"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
)
_EXTENSIONS = {"application/pdf": ".pdf", "image/jpeg": ".jpg", "image/png": ".png"}


@dataclass(frozen=True, slots=True)
class Upload:
    filename: str
    data: bytes


def _problem(message: str, pointer: str) -> InvalidInputError:
    return InvalidInputError(message, (Problem("invalid_input", message, pointer),))


def _clean(value: str | None) -> str | None:
    return (value or "").strip() or None


def sniff(data: bytes) -> str | None:
    """The content type from the file signature (None = not an allowed kind of file)."""
    return next((kind for magic, kind in _SIGNATURES if data.startswith(magic)), None)


def _filename(name: str, content_type: str) -> str:
    base = re.sub(r"[\x00-\x1f\x7f/\\\"]", "_", name.rsplit("/", 1)[-1].rsplit("\\", 1)[-1]).strip() or "document"
    stem = base[:150]
    return stem if stem.lower().endswith(_EXTENSIONS[content_type]) else stem + _EXTENSIONS[content_type]


def _stored_file(knowledge: Knowledge, upload: Upload, now: datetime) -> DocumentFile:
    rules = knowledge.document_rules
    if not upload.data:
        raise _problem("Файл пустой", "file")
    if len(upload.data) > rules.max_upload_bytes:
        raise _problem(f"Файл больше {rules.max_upload_mb} МБ", "file")
    content_type = sniff(upload.data)
    if content_type is None or content_type not in rules.content_types:
        raise _problem("Можно загрузить PDF, JPEG или PNG", "file")
    return DocumentFile(
        id=uuid.uuid4(),
        created_at=now,
        content_type=content_type,
        size_bytes=len(upload.data),
        sha256=hashlib.sha256(upload.data).hexdigest(),
        filename=_filename(upload.filename, content_type),
        data=upload.data,
    )


def _check_date(document_date: date | None, today: date) -> None:
    if document_date is not None and document_date > today:
        raise _problem("Дата документа не может быть в будущем", "document_date")


async def _current_case_id(session: AsyncSession, patient_id: uuid.UUID) -> uuid.UUID | None:
    """The active case, else the latest one (an upload may come before or after a case)."""
    return await session.scalar(
        select(MedicalCase.id)
        .where(MedicalCase.patient_id == patient_id, MedicalCase.demo.is_(False))
        .order_by((MedicalCase.status == CaseStatus.ACTIVE).desc(), MedicalCase.created_at.desc())
        .limit(1)
    )


async def _existing(session: AsyncSession, actor: Actor, submission_id: uuid.UUID) -> Document | None:
    return await session.scalar(
        select(Document).where(Document.created_by == actor.user_id, Document.submission_id == submission_id)
    )


async def upload(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    *,
    submission_id: uuid.UUID,
    kind: str,
    title: str | None,
    document_date: date | None,
    file: Upload,
    now: datetime,
) -> Document:
    """The patient uploads a document from outside; the treating doctor is notified. Idempotent per
    ``submission_id``.

    Raises:
        InvalidInputError: unknown kind, empty/too large/not PDF-JPEG-PNG file, a future date.
    """
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    existing = await _existing(session, actor, submission_id)
    if existing is not None:
        return existing
    rules = knowledge.document_rules
    if kind not in {k.code for k in rules.patient_kinds}:
        raise _problem("Неизвестный вид документа", "kind")
    _check_date(document_date, local_date(now, await patient_timezone(session, actor.patient_id)))
    stored = _stored_file(knowledge, file, now)
    session.add(stored)
    await session.flush()  # the file row first: no ORM relationship orders the inserts
    document = Document(
        id=uuid.uuid4(),
        created_at=now,
        patient_id=actor.patient_id,
        case_id=await _current_case_id(session, actor.patient_id),
        origin=DocumentOrigin.PATIENT,
        kind=kind,
        title=_clean(title) or rules.label(kind) or kind,
        document_date=document_date,
        status=DocumentStatus.UNCONFIRMED,
        file_id=stored.id,
        text=None,
        content=None,
        prescription_id=None,
        created_by=actor.user_id,
        submission_id=submission_id,
        review_note=None,
        reviewed_by=None,
        reviewed_at=None,
        removed_at=None,
    )
    session.add(document)
    await session.flush()
    doctor_user = await session.scalar(
        select(Doctor.user_id)
        .join(Patient, Patient.treating_doctor_id == Doctor.id)
        .where(Patient.id == actor.patient_id)
    )
    if doctor_user is not None:
        await notifications.notify(
            session,
            doctor_user,
            "document_uploaded",
            {"document_id": str(document.id), "patient_id": str(actor.patient_id), "kind": kind},
            now=now,
            patient_id=actor.patient_id,
        )
    await audit.record(
        session,
        actor,
        "document.uploaded",
        entity_type="document",
        entity_id=document.id,
        patient_id=actor.patient_id,
        details={"kind": kind, "content_type": stored.content_type, "size_bytes": stored.size_bytes},
        now=now,
    )
    return document


async def _notify_issued(session: AsyncSession, document: Document, now: datetime) -> None:
    user_id = await session.scalar(select(Patient.user_id).where(Patient.id == document.patient_id))
    if user_id is not None:
        await notifications.notify(
            session,
            user_id,
            "document_issued",
            {"document_id": str(document.id), "kind": document.kind},
            now=now,
            patient_id=document.patient_id,
        )


def _issued(
    actor: Actor,
    patient_id: uuid.UUID,
    case_id: uuid.UUID | None,
    *,
    submission_id: uuid.UUID,
    kind: str,
    title: str,
    document_date: date | None,
    now: datetime,
) -> Document:
    return Document(
        id=uuid.uuid4(),
        created_at=now,
        patient_id=patient_id,
        case_id=case_id,
        origin=DocumentOrigin.DOCTOR,
        kind=kind,
        title=title,
        document_date=document_date,
        status=DocumentStatus.ISSUED,
        file_id=None,
        text=None,
        content=None,
        prescription_id=None,
        created_by=actor.user_id,
        submission_id=submission_id,
        review_note=None,
        reviewed_by=actor.user_id,
        reviewed_at=now,
        removed_at=None,
    )


async def issue(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    *,
    submission_id: uuid.UUID,
    kind: str,
    title: str | None,
    text: str | None,
    document_date: date | None,
    file: Upload | None,
    now: datetime,
) -> Document:
    """The treating doctor issues a visit summary or выписка to the patient: text written for the
    patient and/or a file. Prescription documents go through :func:`issue_prescription`.

    Raises:
        InvalidInputError: unknown kind, neither text nor file, bad file, a future date.
    """
    await require_treating_doctor(session, actor, patient_id)
    existing = await _existing(session, actor, submission_id)
    if existing is not None:
        if existing.patient_id != patient_id:
            raise InvalidStateError("Этот идентификатор отправки уже использован для другого пациента")
        return existing
    rules = knowledge.document_rules
    if kind == "prescription" or kind not in {k.code for k in rules.doctor_kinds}:
        raise _problem("Неизвестный вид документа", "kind")
    body = _clean(text)
    if body is None and file is None:
        raise _problem("Добавьте текст или файл", "text")
    today = local_date(now, await patient_timezone(session, patient_id))
    _check_date(document_date, today)
    document = _issued(
        actor,
        patient_id,
        await _current_case_id(session, patient_id),
        submission_id=submission_id,
        kind=kind,
        title=_clean(title) or rules.label(kind) or kind,
        document_date=document_date or today,
        now=now,
    )
    document.text = body
    if file is not None:
        stored = _stored_file(knowledge, file, now)
        session.add(stored)
        await session.flush()  # the file row first: no ORM relationship orders the inserts
        document.file_id = stored.id
    session.add(document)
    await session.flush()
    await _notify_issued(session, document, now)
    await audit.record(
        session,
        actor,
        "document.issued",
        entity_type="document",
        entity_id=document.id,
        patient_id=patient_id,
        details={"kind": kind, "file": file is not None},
        now=now,
    )
    return document


async def issue_prescription(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    *,
    submission_id: uuid.UUID,
    prescription_id: uuid.UUID | None,
    now: datetime,
) -> Document:
    """The doctor issues a prescription document: a snapshot of one confirmed version (default: the
    latest) with drug, form, doses and dates only — no warnings (D-051). Its status «действует» /
    «истёк» follows the latest end date.

    Raises:
        NotFoundError: no such prescription for this patient, or none at all.
        InvalidStateError: the version has no drug with a regimen.
    """
    await require_treating_doctor(session, actor, patient_id)
    existing = await _existing(session, actor, submission_id)
    if existing is not None:
        if existing.patient_id != patient_id:
            raise InvalidStateError("Этот идентификатор отправки уже использован для другого пациента")
        return existing
    query = (
        select(Prescription)
        .join(MedicalCase, MedicalCase.id == Prescription.case_id)
        .where(MedicalCase.patient_id == patient_id)
    )
    query = (
        query.where(Prescription.id == prescription_id)
        if prescription_id
        else query.order_by(Prescription.confirmed_at.desc()).limit(1)
    )
    prescription = await session.scalar(query)
    if prescription is None:
        raise NotFoundError("Назначение не найдено")
    lines = await prescription_lines(session, prescription.id)
    if not lines:
        raise InvalidStateError("В этой версии назначения нет препаратов с режимом приёма")
    doctor_name = await session.scalar(select(Doctor.full_name).where(Doctor.id == actor.doctor_id))
    tz = await patient_timezone(session, patient_id)
    confirmed_on = local_date(prescription.confirmed_at, tz)
    document = _issued(
        actor,
        patient_id,
        prescription.case_id,
        submission_id=submission_id,
        kind="prescription",
        title=f"Назначение от {confirmed_on:%d.%m.%Y}",
        document_date=confirmed_on,
        now=now,
    )
    document.prescription_id = prescription.id
    document.content = {
        "version": prescription.version,
        "confirmed_on": confirmed_on.isoformat(),
        "doctor_name": doctor_name or "",
        "valid_until": max(line.end for line in lines).isoformat(),
        "items": [
            {
                "trade_name": line.trade_name,
                "form": line.form,
                "intakes": [{"at": at.strftime("%H:%M"), "amount_mg": float(mg)} for at, mg in line.intakes],
                "as_needed": line.as_needed,
                "start": line.start.isoformat(),
                "end": line.end.isoformat(),
            }
            for line in lines
        ],
    }
    session.add(document)
    await session.flush()
    await _notify_issued(session, document, now)
    await audit.record(
        session,
        actor,
        "document.issued",
        entity_type="document",
        entity_id=document.id,
        patient_id=patient_id,
        details={"kind": "prescription", "prescription_id": str(prescription.id)},
        now=now,
    )
    return document


def prescription_state(document: Document, today: date) -> str | None:
    """«действует» / «истёк» for a prescription document (None for other kinds). «использован» (filled
    at a pharmacy) is not tracked."""
    if document.kind != "prescription" or not document.content:
        return None
    return "active" if date.fromisoformat(str(document.content["valid_until"])) >= today else "expired"


async def _doctor_document(session: AsyncSession, actor: Actor, document_id: uuid.UUID) -> Document:
    found = await session.get(Document, document_id)
    if found is None or found.removed_at is not None:
        raise NotFoundError("Документ не найден")
    try:
        await require_treating_doctor(session, actor, found.patient_id)
    except NotFoundError:
        raise NotFoundError("Документ не найден") from None
    locked = await session.get(Document, document_id, with_for_update=True, populate_existing=True)
    assert locked is not None
    return locked


async def review(
    session: AsyncSession,
    actor: Actor,
    document_id: uuid.UUID,
    *,
    decision: DocumentStatus,
    note: str | None,
    now: datetime,
) -> Document:
    """The doctor confirms or rejects a patient upload (a later review replaces the earlier one; both
    audited). The note stays doctor-only; a rejection needs one.

    Raises:
        InvalidInputError: decision other than confirmed/rejected, rejection without a reason.
        InvalidStateError: the document is the doctor's own.
    """
    document = await _doctor_document(session, actor, document_id)
    if document.origin is not DocumentOrigin.PATIENT:
        raise InvalidStateError("Проверяются только документы, загруженные пациентом")
    if decision not in (DocumentStatus.CONFIRMED, DocumentStatus.REJECTED):
        raise _problem("Укажите решение: подтвердить или отклонить", "decision")
    clean = _clean(note)
    if decision is DocumentStatus.REJECTED and clean is None:
        raise _problem("Укажите причину", "note")
    previous = document.status
    document.status, document.review_note = decision, clean
    document.reviewed_by, document.reviewed_at = actor.user_id, now
    await audit.record(
        session,
        actor,
        "document.reviewed",
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
        details={"decision": decision.value, "previous": previous.value},
        now=now,
    )
    return document


async def remove(session: AsyncSession, actor: Actor, document_id: uuid.UUID, *, now: datetime) -> Document:
    """The patient withdraws their own upload while the doctor has not reviewed it yet."""
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    found = await session.get(Document, document_id)
    if found is None or found.patient_id != actor.patient_id or found.removed_at is not None:
        raise NotFoundError("Документ не найден")
    document = await session.get(Document, document_id, with_for_update=True, populate_existing=True)
    assert document is not None
    if document.origin is not DocumentOrigin.PATIENT or document.status is not DocumentStatus.UNCONFIRMED:
        raise InvalidStateError("Документ уже просмотрен врачом — удалить его нельзя")
    document.removed_at = now
    await audit.record(
        session,
        actor,
        "document.removed",
        entity_type="document",
        entity_id=document.id,
        patient_id=document.patient_id,
        details={},
        now=now,
    )
    return document


@dataclass(frozen=True, slots=True)
class DocumentView:
    document: Document
    file: DocumentFile | None  # metadata only (bytes deferred)
    today: date  # the patient's local date, for the prescription status


async def _views(
    session: AsyncSession, patient_id: uuid.UUID, rows: list[Document], now: datetime
) -> list[DocumentView]:
    file_ids = [d.file_id for d in rows if d.file_id]
    files = {f.id: f for f in await session.scalars(select(DocumentFile).where(DocumentFile.id.in_(file_ids)))}
    today = local_date(now, await patient_timezone(session, patient_id))
    return [DocumentView(d, files.get(d.file_id) if d.file_id else None, today) for d in rows]


async def view(session: AsyncSession, document: Document, now: datetime) -> DocumentView:
    return (await _views(session, document.patient_id, [document], now))[0]


async def for_doctor(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, *, now: datetime, offset: int = 0, limit: int = 50
) -> list[DocumentView]:
    """The patient's documents for the doctor: unreviewed uploads first, then newest."""
    await require_treating_doctor(session, actor, patient_id)
    order = case((Document.status == DocumentStatus.UNCONFIRMED, 0), else_=1)
    rows = await session.scalars(
        select(Document)
        .where(Document.patient_id == patient_id, Document.removed_at.is_(None))
        .order_by(order, Document.created_at.desc(), Document.id)
        .offset(offset)
        .limit(limit)
    )
    return await _views(session, patient_id, list(rows.all()), now)


async def for_patient(
    session: AsyncSession, actor: Actor, *, now: datetime, offset: int = 0, limit: int = 50
) -> list[DocumentView]:
    """The patient's own documents (uploads and those from the doctor), newest first."""
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    rows = await session.scalars(
        select(Document)
        .where(Document.patient_id == actor.patient_id, Document.removed_at.is_(None))
        .order_by(Document.created_at.desc(), Document.id)
        .offset(offset)
        .limit(limit)
    )
    return await _views(session, actor.patient_id, list(rows.all()), now)


async def doctor_todo(session: AsyncSession, actor: Actor) -> list[tuple[Document, str]]:
    """Uploads waiting for review across all the doctor's patients: (document, patient name)."""
    if not actor.is_doctor or actor.doctor_id is None:
        raise PermissionDeniedError("Действие доступно только врачу")
    rows = await session.execute(
        select(Document, Patient.full_name)
        .join(Patient, Patient.id == Document.patient_id)
        .where(
            Patient.treating_doctor_id == actor.doctor_id,
            Document.status == DocumentStatus.UNCONFIRMED,
            Document.removed_at.is_(None),
        )
        .order_by(Document.created_at)
        .limit(200)
    )
    return [(d, name) for d, name in rows.all()]


async def get_file(session: AsyncSession, actor: Actor, document_id: uuid.UUID) -> DocumentFile:
    """The file of a document, for its patient or the treating doctor (NotFound for anyone else)."""
    document = await session.get(Document, document_id)
    if document is None or document.removed_at is not None or document.file_id is None:
        raise NotFoundError("Файл не найден")
    try:
        await ensure_patient_access(session, actor, document.patient_id)
    except NotFoundError:
        raise NotFoundError("Файл не найден") from None
    stored = await session.scalar(
        select(DocumentFile).where(DocumentFile.id == document.file_id).options(undefer(DocumentFile.data))
    )
    if stored is None:
        raise NotFoundError("Файл не найден")
    return stored
