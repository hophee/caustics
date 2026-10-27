"""Audit log. Rule (enforced by a test that walks all write endpoints): **every write by a doctor or
patient is audited** in the same transaction as the action."""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.modules.audit.models import AuditEntry


async def record(
    session: AsyncSession,
    actor: Actor | None,
    action: str,
    *,
    entity_type: str,
    entity_id: uuid.UUID,
    patient_id: uuid.UUID | None,
    details: dict[str, Any],
    now: datetime,
) -> None:
    """Add one entry (no commit; the caller's transaction decides)."""
    session.add(
        AuditEntry(
            id=uuid.uuid4(),
            at=now,
            actor_user_id=actor.user_id if actor else None,
            action=action,
            entity_type=entity_type,
            entity_id=entity_id,
            patient_id=patient_id,
            details=details,
        )
    )


async def for_patient(
    session: AsyncSession, patient_id: uuid.UUID, *, offset: int, limit: int
) -> tuple[list[AuditEntry], int]:
    """Entries concerning a patient, newest first (doctor view; access already checked)."""
    where = AuditEntry.patient_id == patient_id
    total = await session.scalar(select(func.count()).select_from(AuditEntry).where(where))
    rows = await session.scalars(
        select(AuditEntry).where(where).order_by(AuditEntry.at.desc()).offset(offset).limit(limit)
    )
    return list(rows.all()), total or 0
