"""Table: adverse_event — НЯ reported by the patient or recorded by the doctor (spec §9).

Reporting an event and judging its link to a drug are separate fields (spec: «сообщение о событии и
оценка его связи с препаратом должны быть разными полями»): the report part is filled by whoever
reports it; the assessment part only by the treating doctor. The system flags when a report to
Росздравнадзор is needed and records when the doctor sent it — it never sends anything itself.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin
from kompas.core.security import Role
from kompas.modules.cases.domain.profile import Tristate


class AeStatus(StrEnum):
    REPORTED = "reported"  # waiting for the doctor
    CONFIRMED = "confirmed"  # «Зафиксировать НЯ»
    DISMISSED = "dismissed"  # not an adverse event (reason in doctor_note)


class DrugRelation(StrEnum):
    """The doctor's causality judgement (simplified WHO-UMC scale)."""

    CERTAIN = "certain"
    PROBABLE = "probable"
    POSSIBLE = "possible"
    UNLIKELY = "unlikely"
    UNRELATED = "unrelated"
    UNCLASSIFIED = "unclassified"


class AdverseEvent(IdMixin, TimestampMixin, Base):
    __tablename__ = "adverse_event"
    __table_args__ = (
        UniqueConstraint("reported_by", "submission_id", name="uq_adverse_event_submission"),
        Index("ix_adverse_event_patient", "patient_id", "created_at"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    # report
    course_id: Mapped[uuid.UUID | None]  # the drug the reporter suspects (None = unknown)
    drug_name: Mapped[str | None]  # snapshot for display
    symptom_code: Mapped[str]
    symptom_text: Mapped[str | None]  # required for «Другое»
    severity: Mapped[str]  # mild | moderate | severe (rules/adverse_events.yaml)
    started_on: Mapped[date | None]
    # Simple patient signal: onset may be approximate; never infer severity or causality.
    simple_report: Mapped[bool] = mapped_column(default=False, server_default="false")
    onset_text: Mapped[str | None]
    details_complete: Mapped[bool] = mapped_column(default=True, server_default="true")
    report_form: Mapped[dict[str, str]] = mapped_column(JSONB, default=dict, server_default="{}")
    ongoing: Mapped[bool]
    ended_on: Mapped[date | None]
    reported_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    reporter_role: Mapped[Role]
    submission_id: Mapped[uuid.UUID]
    last_therapy_change_on: Mapped[date | None]  # latest prescription change on/before started_on
    # doctor's assessment
    status: Mapped[AeStatus]
    relation: Mapped[DrugRelation | None]
    in_label: Mapped[Tristate | None]  # «описано в инструкции» / «не описано — оценить»
    report_required: Mapped[bool]  # извещение Росздравнадзора
    report_sent_on: Mapped[date | None]
    doctor_note: Mapped[str | None]
    reviewed_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    reviewed_at: Mapped[datetime | None]
