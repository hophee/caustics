"""Tables: dose_mark, reminder_rule, reminder_sent.

A mark belongs to one expected dose, identified by the drug's stable ``course_id`` and the dose's
scheduled time (``unique(course_id, scheduled_at)``), so it survives new prescription versions.
Expected doses themselves are not stored: they are computed from the confirmed prescriptions.
``reminder_sent`` makes reminders idempotent across workers (``unique(course_id, scheduled_at, attempt)``).
"""

import uuid
from datetime import datetime

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin
from kompas.modules.adherence.domain.doses import MarkStatus


class DoseMark(IdMixin, TimestampMixin, Base):
    """«Принял» (with the actual time) or «Пропустил» (with a reason) for one dose."""

    __tablename__ = "dose_mark"
    __table_args__ = (
        UniqueConstraint("course_id", "scheduled_at", name="uq_dose_mark_dose"),
        Index("ix_dose_mark_patient_scheduled", "patient_id", "scheduled_at"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    prescription_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("prescription.id"))  # version in force
    course_id: Mapped[uuid.UUID]
    scheduled_at: Mapped[datetime]
    status: Mapped[MarkStatus]
    taken_at: Mapped[datetime | None]
    reason: Mapped[str | None]  # skip reason code (rules/adherence.yaml)
    submission_id: Mapped[uuid.UUID]  # the submission that set the current state
    updated_at: Mapped[datetime]


class DoseMarkSubmission(IdMixin, TimestampMixin, Base):
    """Every mark submission, append-only: idempotency for each ``submission_id`` (a retry of an older
    submission returns the current mark instead of re-applying the old state) and the mark history."""

    __tablename__ = "dose_mark_submission"
    __table_args__ = (UniqueConstraint("patient_id", "submission_id", name="uq_dose_mark_submission"),)

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    submission_id: Mapped[uuid.UUID]
    mark_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("dose_mark.id"))
    status: Mapped[MarkStatus]
    taken_at: Mapped[datetime | None]
    reason: Mapped[str | None]


class ReminderRule(Base):
    """The patient's one reminder rule (A15); absent row = the defaults from ``adherence.yaml``."""

    __tablename__ = "reminder_rule"

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"), primary_key=True)
    enabled: Mapped[bool]
    first_offset_minutes: Mapped[int]
    repeat_every_minutes: Mapped[int]
    max_repeats: Mapped[int]
    updated_at: Mapped[datetime]


class ReminderSent(IdMixin, Base):
    """One reminder actually sent for one dose."""

    __tablename__ = "reminder_sent"
    __table_args__ = (UniqueConstraint("course_id", "scheduled_at", "attempt", name="uq_reminder_sent_attempt"),)

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    course_id: Mapped[uuid.UUID]
    scheduled_at: Mapped[datetime]
    attempt: Mapped[int]
    sent_at: Mapped[datetime]
