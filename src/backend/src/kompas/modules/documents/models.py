"""Tables: document, document_file — patient documents (spec §11 «Документы пациента»).

Two origins. The **patient** uploads a PDF or photo from another clinic; it is «не подтверждено» until
the treating doctor reviews it (confirm / reject, with a doctor-only note). The **doctor** issues
documents to the patient: a visit summary or a discharge summary (выписка) as text and/or a file, and
a prescription document built from a confirmed prescription version (a snapshot; the website renders a
printable page, no server-side PDF). Certificates (справки) are out of scope (D-111); nothing is
extracted from uploads automatically.

File bytes live in their own table (Postgres ``bytea``, size-limited by ``rules/documents.yaml``), so
listing documents never loads them; one store, covered by the same backups.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, LargeBinary, UniqueConstraint
from sqlalchemy.orm import Mapped, deferred, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin


class DocumentOrigin(StrEnum):
    PATIENT = "patient"
    DOCTOR = "doctor"


class DocumentStatus(StrEnum):
    UNCONFIRMED = "unconfirmed"  # patient upload, not reviewed yet («не подтверждено»)
    CONFIRMED = "confirmed"  # the doctor reviewed and accepted it
    REJECTED = "rejected"  # the doctor reviewed it: not relevant / unreadable (reason doctor-only)
    ISSUED = "issued"  # a document from the doctor


class DocumentFile(IdMixin, TimestampMixin, Base):
    __tablename__ = "document_file"

    content_type: Mapped[str]
    size_bytes: Mapped[int]
    sha256: Mapped[str]
    filename: Mapped[str]  # sanitised original name, for downloads
    data: Mapped[bytes] = deferred(mapped_column(LargeBinary))


class Document(IdMixin, TimestampMixin, Base):
    __tablename__ = "document"
    __table_args__ = (
        UniqueConstraint("created_by", "submission_id", name="uq_document_submission"),
        Index("ix_document_patient_created", "patient_id", "created_at"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("medical_case.id"))
    origin: Mapped[DocumentOrigin]
    kind: Mapped[str]  # rules/documents.yaml patient_kinds / doctor_kinds
    title: Mapped[str]
    document_date: Mapped[date | None]  # the date on the document (patient's word for uploads)
    status: Mapped[DocumentStatus]
    file_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("document_file.id"))
    text: Mapped[str | None]  # doctor documents: the body written for the patient
    content: Mapped[dict[str, Any] | None]  # prescription document: snapshot of the version
    prescription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prescription.id"))
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    submission_id: Mapped[uuid.UUID]
    review_note: Mapped[str | None]  # doctor-only
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    reviewed_at: Mapped[datetime | None]
    removed_at: Mapped[datetime | None]  # the patient withdrew an unreviewed upload
