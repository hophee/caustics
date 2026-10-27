"""Alert lifecycle (D-014): created → sent → delivered → accepted → handled.

Technical delivery is not a clinical response: the patient interface may say a doctor has seen the
signal only once it is ``accepted`` (spec §10). Retries keep the alert in ``sent``.
"""

from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum

from kompas.core.errors import InvalidInputError, InvalidStateError


class AlertStatus(StrEnum):
    CREATED = "created"
    SENT = "sent"
    DELIVERED = "delivered"
    ACCEPTED = "accepted"
    HANDLED = "handled"


class AlertEvent(StrEnum):
    DISPATCH = "dispatch"  # worker sent it
    DELIVERY_CONFIRMED = "delivery_confirmed"  # doctor's browser acknowledged
    ACCEPT = "accept"  # doctor opened it
    HANDLE = "handle"  # doctor recorded the action taken


@dataclass(frozen=True, slots=True)
class AlertState:
    """Current status and when each step happened."""

    status: AlertStatus
    created_at: datetime
    sent_at: datetime | None = None
    delivered_at: datetime | None = None
    accepted_at: datetime | None = None
    handled_at: datetime | None = None
    handled_note: str | None = None


def transition(state: AlertState, event: AlertEvent, at: datetime, note: str | None = None) -> AlertState:
    """Apply an event. Allowed: created→sent (DISPATCH), sent→sent (DISPATCH, retry),
    sent→delivered, delivered→accepted, accepted→handled (HANDLE needs a non-empty ``note``).
    ACCEPT is also allowed from created and sent (the doctor can open the alert as soon as it is
    written, before dispatch or the delivery acknowledgement).

    Raises:
        InvalidStateError: the event is not allowed in the current status.
        InvalidInputError: HANDLE without a note.
    """
    s, e = state.status, event
    if e is AlertEvent.DISPATCH and s in (AlertStatus.CREATED, AlertStatus.SENT):
        return replace(state, status=AlertStatus.SENT, sent_at=state.sent_at or at)
    if e is AlertEvent.DELIVERY_CONFIRMED and s is AlertStatus.SENT:
        return replace(state, status=AlertStatus.DELIVERED, delivered_at=at)
    if e is AlertEvent.DELIVERY_CONFIRMED and s in (AlertStatus.ACCEPTED, AlertStatus.HANDLED):
        return replace(state, delivered_at=state.delivered_at or at)  # late ack: record, keep status
    if e is AlertEvent.ACCEPT and s in (AlertStatus.CREATED, AlertStatus.SENT, AlertStatus.DELIVERED):
        return replace(state, status=AlertStatus.ACCEPTED, accepted_at=at)
    if e is AlertEvent.HANDLE and s is AlertStatus.ACCEPTED:
        if not (note or "").strip():
            raise InvalidInputError("Опишите, что сделано по сигналу")
        return replace(state, status=AlertStatus.HANDLED, handled_at=at, handled_note=note.strip() if note else None)
    raise InvalidStateError(f"Действие {e} недоступно для сигнала в статусе {s}")


def patient_may_see_doctor_received(state: AlertState) -> bool:
    """True only from ``accepted`` on."""
    return state.status in (AlertStatus.ACCEPTED, AlertStatus.HANDLED)
