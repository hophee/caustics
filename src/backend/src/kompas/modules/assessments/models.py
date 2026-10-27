"""Tables: assignment, response, result.

Raw answers are always stored — also when they fail validation (status ``rejected``), so a critical
alert always has the answers behind it. One result per complete response (``unique(response_id)``);
it snapshots the band used at scoring time, so it renders without the (possibly newer) scale
definition. Results are doctor-only: the patient never sees them (D-109).
Indexes: ``response(case_id, scale_code, taken_at)``; ``response(filled_by_user, submission_id)``
unique (idempotency scoped to the caller, so a leaked id can't replay someone else's response);
``assignment(case_id) WHERE active``.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin
from kompas.modules.knowledge.scales import FilledBy


class AssignmentMode(StrEnum):
    ONCE = "once"
    REPEAT = "repeat"


class Assignment(IdMixin, TimestampMixin, Base):
    """A doctor asks the patient to fill a patient scale once or every N days."""

    __tablename__ = "assignment"
    __table_args__ = (Index("ix_assignment_active_case", "case_id", postgresql_where=text("active")),)

    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    scale_code: Mapped[str]
    mode: Mapped[AssignmentMode]
    every_days: Mapped[int | None]
    starts_on: Mapped[date]
    due_days: Mapped[int]
    active: Mapped[bool]
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))


class ResponseStatus(StrEnum):
    COMPLETE = "complete"
    REJECTED = "rejected"  # failed validation; kept for the record and for critical alerts


class Response(IdMixin, TimestampMixin, Base):
    """One submission (patient self-report or clinician rating)."""

    __tablename__ = "response"
    __table_args__ = (
        UniqueConstraint("filled_by_user", "submission_id", name="uq_response_submission"),
        Index("ix_response_case_scale_taken", "case_id", "scale_code", "taken_at"),
    )

    submission_id: Mapped[uuid.UUID]  # client-generated; unique per filled_by_user
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    assignment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("assignment.id"))
    scale_code: Mapped[str]
    scale_version: Mapped[int]
    filled_by: Mapped[FilledBy]
    filled_by_user: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    taken_at: Mapped[datetime]  # server time for patients
    answers: Mapped[dict[str, int]]
    status: Mapped[ResponseStatus]
    problems: Mapped[list[dict[str, Any]]]  # validation problems when rejected


class Result(IdMixin, TimestampMixin, Base):
    """Stored scored outcome."""

    __tablename__ = "result"

    response_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("response.id"), unique=True)
    total: Mapped[float | None]
    band: Mapped[dict[str, Any] | None]
    subscores: Mapped[list[dict[str, Any]]]
    critical_hits: Mapped[list[dict[str, Any]]]
    explanation: Mapped[list[str]]
