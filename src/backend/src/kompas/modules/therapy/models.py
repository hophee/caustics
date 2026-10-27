"""Tables: prescription (versions), prescription_item, patient_reported_drug.

Only confirmed prescriptions are stored (D-040). Each confirmation creates a new version holding the
full resulting regimen (D-061) plus the snapshot: warnings at confirmation, justifications, justified
warning ids (carried into the next version, D-072), the knowledge version (D-049) and the safety
profile the checks used.

A drug keeps a stable ``course_id`` across versions (a continued drug is re-inserted with the same
course); ``CurrentTherapyItem.ref`` = course id, so drafts, stops, warning ids of label-less drugs and
(later) dose marks stay valid when a new version is confirmed.
Indexes: ``prescription(case_id, version)`` unique; ``prescription_item(prescription_id)``;
``patient_reported_drug(patient_id) WHERE confirmed_at IS NULL AND removed_at IS NULL``.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin


class Prescription(IdMixin, TimestampMixin, Base):
    """One confirmed version."""

    __tablename__ = "prescription"
    __table_args__ = (UniqueConstraint("case_id", "version", name="uq_prescription_case_version"),)

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    version: Mapped[int]
    previous_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prescription.id"))
    confirmed_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    confirmed_at: Mapped[datetime]
    knowledge_version: Mapped[str]
    profile_snapshot: Mapped[dict[str, Any]]
    warnings: Mapped[list[dict[str, Any]]]
    justifications: Mapped[list[dict[str, str]]]  # [{warning_id, text}]
    justified_warning_ids: Mapped[list[str]]
    stopped_course_ids: Mapped[list[str]]


class PrescriptionItem(IdMixin, Base):
    """A drug in a version."""

    __tablename__ = "prescription_item"

    prescription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prescription.id"), index=True)
    course_id: Mapped[uuid.UUID]
    label_id: Mapped[str | None]
    substance: Mapped[str | None]
    trade_name: Mapped[str]  # snapshot at confirmation (patient view needs no catalog, D-049)
    form: Mapped[str]
    regimen: Mapped[dict[str, Any] | None]
    is_new: Mapped[bool]
    # set when a patient-reported drug was confirmed into it
    reported_drug_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("patient_reported_drug.id"))
    provenance: Mapped[dict[str, Any] | None]
    suggested_dose_text: Mapped[str | None]


class CurrentTherapyReview(IdMixin, TimestampMixin, Base):
    """Doctor's reconciliation of actual medication use; prescriptions stay immutable."""

    __tablename__ = "current_therapy_review"
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("medical_case.id"), index=True)
    prescription_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("prescription.id"))
    recorded_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    items: Mapped[list[dict[str, Any]]]
    reviewed_reported_ids: Mapped[list[str]]


class PatientReportedDrug(IdMixin, TimestampMixin, Base):
    """A drug the patient says they take (D-050): part of current therapy (``reported:<id>``) until a
    confirmation keeps it (``confirmed_at``, becomes a prescription item) or stops it (``removed_at``)."""

    __tablename__ = "patient_reported_drug"
    __table_args__ = (
        UniqueConstraint("patient_id", "submission_id", name="uq_patient_reported_drug_submission"),
        Index(
            "ix_patient_reported_drug_open",
            "patient_id",
            postgresql_where=text("confirmed_at IS NULL AND removed_at IS NULL"),
        ),
    )

    submission_id: Mapped[uuid.UUID]  # unique per patient: (patient_id, submission_id)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    label_id: Mapped[str | None]
    substance: Mapped[str | None]
    regimen: Mapped[dict[str, Any] | None]
    confirmed_at: Mapped[datetime | None]
    removed_at: Mapped[datetime | None]
