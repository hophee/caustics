"""Table: audit_entry (append-only). Index: ``(patient_id, at desc)``."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import Index
from sqlalchemy.orm import Mapped

from kompas.core.db import Base, IdMixin


class AuditEntry(IdMixin, Base):
    """Who did what to which object, when. ``details`` holds ids, codes and short values (warning id +
    text for justified red warnings, D-046); never free text written by patients (privacy)."""

    __tablename__ = "audit_entry"
    __table_args__ = (Index("ix_audit_entry_patient_at", "patient_id", "at"),)

    at: Mapped[datetime]
    actor_user_id: Mapped[uuid.UUID | None]  # None = system (worker)
    action: Mapped[str]  # e.g. "prescription.confirmed", "response.submitted", "alert.accepted"
    entity_type: Mapped[str]
    entity_id: Mapped[uuid.UUID]
    patient_id: Mapped[uuid.UUID | None]
    details: Mapped[dict[str, Any]]
