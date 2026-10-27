"""Tables: patient_profile, medical_case, visit, diagnosis.

Indexes: ``medical_case(patient_id) WHERE status='active' AND NOT demo`` unique (one real active case
per patient, A02; labelled demo cases don't count); ``diagnosis(case_id) WHERE is_main AND status <>
'removed'`` unique; ``visit(case_id, held_at)``.
List facts keep unknown-vs-none (D-063): NULL = not asked, empty array = none.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin
from kompas.modules.cases.domain.profile import Sex, SuicideRisk, Tristate


class PatientProfileRow(Base):
    """Safety profile (1:1 with patient, created together with the patient card).

    ``current_medications_reviewed_at``: when the doctor last confirmed the current-medication list
    (NULL = never asked; an empty current therapy is only meaningful after a review). The domain
    checks don't use it yet (known limitation, D-073).
    """

    __tablename__ = "patient_profile"

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"), primary_key=True)
    sex: Mapped[Sex]
    timezone: Mapped[str]
    pregnant: Mapped[Tristate]
    lactating: Mapped[Tristate]
    substance_use: Mapped[Tristate]
    suicide_risk: Mapped[SuicideRisk]
    diagnoses: Mapped[list[str]]  # comorbid ICD codes; main diagnoses live in `diagnosis`
    allergies: Mapped[list[str] | None]
    intolerant_excipients: Mapped[list[str] | None]
    past_intolerance: Mapped[list[str] | None]
    past_inefficacy: Mapped[list[str] | None]
    current_medications_reviewed_at: Mapped[datetime | None]
    weight_kg: Mapped[float | None]
    updated_at: Mapped[datetime]
    updated_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))


class CaseStatus(StrEnum):
    ACTIVE = "active"
    CLOSED = "closed"


class MedicalCase(IdMixin, TimestampMixin, Base):
    """A medical case. The doctor is the patient's treating doctor (no copy here)."""

    __tablename__ = "medical_case"
    __table_args__ = (
        Index(
            "uq_medical_case_one_active",
            "patient_id",
            unique=True,
            postgresql_where=text("status = 'active' AND NOT demo"),
        ),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"), index=True)
    status: Mapped[CaseStatus]
    closed_at: Mapped[datetime | None]
    closed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    demo: Mapped[bool]


class VisitKind(StrEnum):
    FIRST = "first"
    REPEAT = "repeat"


class Visit(IdMixin, TimestampMixin, Base):
    """A visit; structured content as JSONB until the visit form is fixed."""

    __tablename__ = "visit"
    __table_args__ = (Index("ix_visit_case_held", "case_id", "held_at"),)

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    kind: Mapped[VisitKind]
    held_at: Mapped[datetime]
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctor.id"))
    content: Mapped[dict[str, object]]


class DiagnosisStatus(StrEnum):
    PROVISIONAL = "provisional"
    ESTABLISHED = "established"
    REMOVED = "removed"


class Diagnosis(IdMixin, TimestampMixin, Base):
    """Diagnosis of a case; full ICD code kept, original spelling too."""

    __tablename__ = "diagnosis"
    __table_args__ = (
        Index(
            "uq_diagnosis_one_main",
            "case_id",
            unique=True,
            postgresql_where=text("is_main AND status <> 'removed'"),
        ),
    )

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"), index=True)
    icd_code: Mapped[str]
    icd_original: Mapped[str]
    is_main: Mapped[bool]
    status: Mapped[DiagnosisStatus]
