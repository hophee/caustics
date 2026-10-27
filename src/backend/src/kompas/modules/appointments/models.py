"""Tables: appointment, appointment_change_request, appointment_reminder — planned visits (spec §11).

A held visit (the doctor's clinical record) lives in ``visit`` (cases module); an appointment is the
*plan* the patient sees: when, where, which kind. The doctor creates and changes it; the patient can
only ask to reschedule or cancel, and the doctor decides. Reminders are recorded per start time and
offset, so a moved visit gets fresh reminders and none is sent twice.
"""

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin
from kompas.modules.cases.models import VisitKind


class AppointmentStatus(StrEnum):
    PLANNED = "planned"
    CANCELLED = "cancelled"
    COMPLETED = "completed"


class AppointmentMode(StrEnum):
    IN_PERSON = "in_person"
    ONLINE = "online"  # a link only; video calls are out of MVP (spec §13)


class Appointment(IdMixin, TimestampMixin, Base):
    __tablename__ = "appointment"
    __table_args__ = (
        UniqueConstraint("created_by", "submission_id", name="uq_appointment_submission"),
        Index("ix_appointment_patient_start", "patient_id", "starts_at"),
        Index("ix_appointment_doctor_start", "doctor_id", "starts_at"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctor.id"))
    starts_at: Mapped[datetime]
    duration_minutes: Mapped[int]
    kind: Mapped[VisitKind]
    mode: Mapped[AppointmentMode]
    place: Mapped[str | None]  # кабинет / адрес (in person)
    link: Mapped[str | None]  # online
    purpose: Mapped[str | None]  # doctor-only («Цель»)
    note_for_patient: Mapped[str | None]  # e.g. «возьмите выписку из стационара»
    status: Mapped[AppointmentStatus]
    cancel_reason: Mapped[str | None]
    visit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("visit.id"))
    patient_summary: Mapped[str | None]  # written by the doctor for the patient after the visit
    created_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    submission_id: Mapped[uuid.UUID]
    updated_at: Mapped[datetime]


class RequestKind(StrEnum):
    RESCHEDULE = "reschedule"
    CANCEL = "cancel"


class RequestStatus(StrEnum):
    PENDING = "pending"
    ACCEPTED = "accepted"
    DECLINED = "declined"
    CLOSED = "closed"  # the visit was cancelled or completed before the doctor decided


class AppointmentChangeRequest(IdMixin, TimestampMixin, Base):
    __tablename__ = "appointment_change_request"
    __table_args__ = (
        UniqueConstraint("patient_id", "submission_id", name="uq_appointment_change_request_submission"),
        Index(
            "uq_appointment_change_request_pending",
            "appointment_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
        ),
    )

    appointment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appointment.id"), index=True)
    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    kind: Mapped[RequestKind]
    proposed_starts_at: Mapped[datetime | None]  # reschedule: the patient's wish (optional)
    reason: Mapped[str | None]
    submission_id: Mapped[uuid.UUID]
    status: Mapped[RequestStatus]
    reply: Mapped[str | None]  # the doctor's answer, shown to the patient
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    decided_at: Mapped[datetime | None]


class AppointmentReminder(IdMixin, Base):
    __tablename__ = "appointment_reminder"
    __table_args__ = (UniqueConstraint("appointment_id", "starts_at", "code", name="uq_appointment_reminder_send"),)

    appointment_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("appointment.id"))
    starts_at: Mapped[datetime]  # the start time the reminder was for
    code: Mapped[str]  # rules/appointments.yaml reminders[].code
    sent_at: Mapped[datetime]
