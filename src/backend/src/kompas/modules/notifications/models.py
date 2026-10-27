"""Tables: notification, stream_ticket.

Indexes: ``notification(user_id, seq)``; ``stream_ticket(ticket_hash)`` unique.
"""

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import BigInteger, ForeignKey, Identity, Index
from sqlalchemy.orm import Mapped, mapped_column

from kompas.core.db import Base, IdMixin, TimestampMixin


class Notification(IdMixin, TimestampMixin, Base):
    """One in-app message. ``seq`` (identity) is the SSE event id; per-user ordering is guaranteed by
    an advisory transaction lock in ``notify`` (inserts for one user commit in seq order)."""

    __tablename__ = "notification"
    __table_args__ = (Index("ix_notification_user_seq", "user_id", "seq"),)
    __mapper_args__ = {"eager_defaults": True}  # noqa: RUF012  (reads the identity seq back via RETURNING)

    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    kind: Mapped[str]  # "alert", "prescription", "reported_drug"
    payload: Mapped[dict[str, Any]]
    seq: Mapped[int] = mapped_column(BigInteger, Identity(), unique=True)
    read_at: Mapped[datetime | None]
    patient_id: Mapped[uuid.UUID | None]  # the patient it concerns (hidden on transfer)
    hidden_at: Mapped[datetime | None]  # set when the user lost access to that patient


class StreamTicket(IdMixin, TimestampMixin, Base):
    """Single-use, 60 s ticket for opening an SSE stream (EventSource can't send headers; no JWT in
    URLs). Consumed atomically: ``UPDATE … SET used_at=now WHERE ticket_hash=:h AND used_at IS NULL
    AND expires_at > now RETURNING user_id``."""

    __tablename__ = "stream_ticket"

    ticket_hash: Mapped[str] = mapped_column(unique=True)
    user_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("user_account.id"))
    expires_at: Mapped[datetime]
    used_at: Mapped[datetime | None]
