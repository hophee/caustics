"""Tables: referral_recommendation, referral (spec §10 «Направление к психиатру», F8).

Kept separate on purpose (spec MVP addition): the urgency of a signal, the recommendation category
«обязательно / желательно» with its reason, the doctor's decision, and the issued referral.
The system creates recommendations only; a referral exists only after the doctor issues it.
"""

import uuid
from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin
from kompas.modules.knowledge.care_rules import ReferralCategory


class RecommendationStatus(StrEnum):
    OPEN = "open"
    ACCEPTED = "accepted"
    DECLINED = "declined"


class ReferralRecommendation(IdMixin, TimestampMixin, Base):
    __tablename__ = "referral_recommendation"
    __table_args__ = (
        Index("ix_referral_recommendation_patient", "patient_id", "created_at"),
        UniqueConstraint("created_by", "submission_id", name="uq_referral_recommendation_submission"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    category: Mapped[ReferralCategory]
    reason: Mapped[str]
    rule_code: Mapped[str | None]  # None = recommended by the doctor by hand
    source: Mapped[str | None]  # "scale:PHQ-9", "diary"
    source_id: Mapped[uuid.UUID | None]
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))  # None = system
    submission_id: Mapped[uuid.UUID | None]  # doctor's client-generated id (None for rule-made ones)
    dedup_key: Mapped[str | None] = mapped_column(unique=True)
    status: Mapped[RecommendationStatus]
    decided_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("user_account.id"))
    decided_at: Mapped[datetime | None]
    decision_reason: Mapped[str | None]


class ReferralKind(StrEnum):
    CONSULTATION = "consultation"
    EXAMINATION = "examination"
    PROCEDURE = "procedure"
    HOSPITALIZATION = "hospitalization"


class ReferralUrgency(StrEnum):
    PLANNED = "planned"
    PRIORITY = "priority"
    EMERGENCY = "emergency"


class ReferralStatus(StrEnum):
    ISSUED = "issued"
    CANCELLED = "cancelled"


class Referral(IdMixin, TimestampMixin, Base):
    """An issued referral (the form of spec §10)."""

    __tablename__ = "referral"
    __table_args__ = (
        Index("ix_referral_patient", "patient_id", "created_at"),
        UniqueConstraint("issued_by", "submission_id", name="uq_referral_submission"),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("medical_case.id"))
    # one referral per recommendation
    recommendation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("referral_recommendation.id"), unique=True)
    submission_id: Mapped[uuid.UUID]
    kind: Mapped[ReferralKind]
    specialist: Mapped[str]  # профиль специалиста, e.g. "психиатр"
    category: Mapped[ReferralCategory | None]
    urgency: Mapped[ReferralUrgency]
    clinical_reason: Mapped[str]
    diagnosis_code: Mapped[str | None]
    valid_until: Mapped[date]
    issued_by: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctor.id"))
    status: Mapped[ReferralStatus]
    cancelled_at: Mapped[datetime | None]
    cancel_reason: Mapped[str | None]
