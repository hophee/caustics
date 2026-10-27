"""Tables: user_account, doctor, patient.

MVP: exactly one treating doctor per patient (A02/D-001) → ``patient.treating_doctor_id NOT NULL``;
a transfer is an audited update. A patient card exists before the patient has an account: the doctor
creates it and hands over a one-time invite code; ``user_id`` is set at activation.

Indexes: ``user_account(lower(email))`` unique; ``patient(invite_hmac)`` unique where not null;
``patient(treating_doctor_id)``; ``doctor(user_id)`` / ``patient(user_id)`` unique.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import CheckConstraint, ForeignKey, Index, String, func, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, column_property, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin
from kompas.core.security import Role


class User(IdMixin, TimestampMixin, Base):
    """A login. Email is unique case-insensitively."""

    __tablename__ = "user_account"
    __table_args__ = (Index("uq_user_account_email_lower", func.lower(text("email")), unique=True),)

    email: Mapped[str]
    password_hash: Mapped[str]
    password_changed_at: Mapped[datetime]  # tokens issued before this are rejected
    role: Mapped[Role]
    is_active: Mapped[bool]
    full_name: Mapped[str | None]  # given at registration (doctor/patient rows keep their own name)
    demo: Mapped[bool] = mapped_column(server_default="false")  # throwaway demo-mode account


class Doctor(IdMixin, TimestampMixin, Base):
    __tablename__ = "doctor"

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"), unique=True)
    full_name: Mapped[str]
    specialty: Mapped[str]


class Patient(IdMixin, TimestampMixin, Base):
    """Identity of a patient (synthetic data only, D-007). Safety facts: ``cases.PatientProfileRow``.

    ``invite_hmac``: HMAC-SHA256 (server secret) of a high-entropy one-time code (≥ 10 base32 chars),
    so activation is an indexed lookup; cleared at activation.
    """

    __tablename__ = "patient"
    __table_args__ = (
        Index("uq_patient_invite_hmac", "invite_hmac", unique=True, postgresql_where=text("invite_hmac IS NOT NULL")),
        CheckConstraint("jsonb_array_length(emergency_contacts) <= 5", name="emergency_contacts_limit"),
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"), unique=True)
    treating_doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctor.id"), index=True)
    last_name: Mapped[str] = mapped_column(String(60))
    first_name: Mapped[str] = mapped_column(String(60))
    middle_name: Mapped[str] = mapped_column(String(60), default="", server_default="")
    full_name: Mapped[str] = column_property(func.trim(last_name + " " + first_name + " " + middle_name))
    birth_date: Mapped[date]
    emergency_contacts: Mapped[list[dict[str, str]]] = mapped_column(JSONB, default=list, server_default="[]")
    invite_hmac: Mapped[str | None]
    invite_expires_at: Mapped[datetime | None]
