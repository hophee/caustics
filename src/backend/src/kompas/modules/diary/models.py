"""Tables: diary_entry, diary_entry_revision, diary_triage, diary_note.

What the patient wrote is never overwritten (D-015): an entry (one per case and local day) has
append-only revisions; the latest is shown. Triage belongs to a revision; a model verdict for an older
revision is dropped. Doctor notes live in their own table, so a patient payload can't include them.
Indexes: ``diary_entry(case_id, day)`` unique; ``diary_entry_revision(entry_id, revision)`` unique;
``diary_entry_revision(patient_id, submission_id)`` unique; ``diary_note(entry_id)``.
"""

import uuid
from datetime import date, datetime
from typing import Any

from sqlalchemy import ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin
from kompas.modules.diary.domain.entry import Sleep
from kompas.modules.knowledge.triage_rules import TriageLevel


class DiaryEntryRow(IdMixin, TimestampMixin, Base):
    """The day's entry; ``current_revision`` points at the latest revision number."""

    __tablename__ = "diary_entry"
    __table_args__ = (UniqueConstraint("case_id", "day", name="uq_diary_entry_case_day"),)

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    day: Mapped[date]
    current_revision: Mapped[int]


class DiaryEntryRevision(IdMixin, TimestampMixin, Base):
    """One saved version of the entry."""

    __tablename__ = "diary_entry_revision"
    __table_args__ = (
        UniqueConstraint("entry_id", "revision", name="uq_diary_entry_revision_revision"),
        UniqueConstraint("patient_id", "submission_id", name="uq_diary_entry_revision_submission"),
    )

    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("diary_entry.id"))
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))  # scopes submission_id
    revision: Mapped[int]
    submission_id: Mapped[uuid.UUID]  # client-generated; unique per patient
    schema_version: Mapped[int] = mapped_column(default=1, server_default="1")
    irritability: Mapped[int | None]
    sleep_hours: Mapped[float | None]
    unusual_experiences: Mapped[bool | None]
    mood: Mapped[int | None]
    anxiety: Mapped[int | None]
    energy: Mapped[int | None]
    sleep: Mapped[Sleep | None]
    complaints: Mapped[list[str]]
    side_effects: Mapped[list[str]]
    self_harm_thoughts: Mapped[bool | None]
    text: Mapped[str | None]


class DiaryTriageRow(IdMixin, TimestampMixin, Base):
    """Triage of one revision (rules + keywords at save; model verdict merged by the worker)."""

    __tablename__ = "diary_triage"

    revision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("diary_entry_revision.id"), unique=True)
    level: Mapped[TriageLevel]
    reasons: Mapped[list[dict[str, Any]]]
    critical_hits: Mapped[list[dict[str, Any]]]
    knowledge_version: Mapped[str]
    model_name: Mapped[str | None]
    model_version: Mapped[str | None]
    model_unsure: Mapped[bool]
    model_unavailable: Mapped[bool]
    model_pending: Mapped[bool]
    updated_at: Mapped[datetime]


class DiaryNote(IdMixin, TimestampMixin, Base):
    """Doctor's private note on an entry (spec §2); never returned to the patient."""

    __tablename__ = "diary_note"

    entry_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("diary_entry.id"), index=True)
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctor.id"))
    text: Mapped[str]
