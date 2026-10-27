"""Table: alert. Lifecycle follows ``monitoring.domain.alerts`` (created → sent → delivered →
accepted → handled).

``dedup_key`` is for idempotency only — it identifies the source event and rule
(``response:<id>:item:9``, ``diary:<revision_id>:self_harm``), never just "patient + rule", so a new
critical answer always raises a new alert even while an older one is open. Inserted with
``INSERT … ON CONFLICT (dedup_key) DO NOTHING RETURNING`` (+ select) so concurrent duplicates neither
fail the request nor drop the alert.
Indexes: ``alert(dedup_key)`` unique; ``alert(doctor_id, level, created_at) WHERE status <> 'handled'``;
``alert(next_retry_at) WHERE status = 'sent'``.
"""

import uuid
from datetime import datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin
from kompas.modules.knowledge.triage_rules import UrgentScenario
from kompas.modules.monitoring.domain.alerts import AlertStatus


class AlertLevel(StrEnum):
    """Spec §10 levels; critical answers and diary «Срочно» are level 1."""

    URGENT = "urgent"
    HIGH = "high"
    PLANNED = "planned"


class AlertRow(IdMixin, TimestampMixin, Base):
    __tablename__ = "alert"
    __table_args__ = (
        Index("ix_alert_open", "doctor_id", "level", "created_at", postgresql_where=text("status <> 'handled'")),
        Index("ix_alert_retry", "next_retry_at", postgresql_where=text("status = 'sent'")),
    )

    patient_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("patient.id"), index=True)
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("medical_case.id"))
    # routing snapshot; moved on transfer
    doctor_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("doctor.id"))
    level: Mapped[AlertLevel]
    # urgent only: «опасность сейчас» / «уточнить состояние» (D-132); None for high/planned
    scenario: Mapped[UrgentScenario | None]
    source: Mapped[str]  # "scale:PHQ-9", "diary", "diary_model"
    source_id: Mapped[uuid.UUID]  # response / diary revision id (the evidence behind the alert)
    reason: Mapped[str]
    details: Mapped[dict[str, Any]]
    knowledge_version: Mapped[str]
    dedup_key: Mapped[str] = mapped_column(unique=True)
    status: Mapped[AlertStatus]
    # the in-app notification re-sent on retries
    notification_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("notification.id"))
    attempts: Mapped[int]
    next_retry_at: Mapped[datetime | None]
    undelivered: Mapped[bool]  # retries exhausted without a delivery ack: shown prominently
    sent_at: Mapped[datetime | None]
    delivered_at: Mapped[datetime | None]
    accepted_at: Mapped[datetime | None]
    handled_at: Mapped[datetime | None]
    handled_note: Mapped[str | None]
