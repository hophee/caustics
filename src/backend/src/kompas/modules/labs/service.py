"""Demo lab values (A13): the treating doctor enters and removes them; the basket checks read them.

Doctor-only: lab values are results, and the patient never sees results (D-109). Input is checked
against the plausible range of each test in ``rules/labs.yaml`` (typos), never against clinical limits.
"""

import uuid
from datetime import date, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.core.clock import local_date
from kompas.core.errors import InvalidInputError, InvalidStateError, NotFoundError, Problem
from kompas.modules.audit import service as audit
from kompas.modules.cases.service import patient_timezone, require_treating_doctor
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.labs.domain import LabValue
from kompas.modules.labs.models import DemoLabValue

MAX_AGE_DAYS = 5 * 365  # older results are refused as input


def _problem(message: str, pointer: str) -> InvalidInputError:
    return InvalidInputError(message, (Problem("invalid_input", message, pointer),))


async def add(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    patient_id: uuid.UUID,
    *,
    submission_id: uuid.UUID,
    code: str,
    value: float,
    measured_on: date,
    now: datetime,
) -> DemoLabValue:
    """The treating doctor enters a demo lab value. Idempotent per ``submission_id``.

    Raises:
        InvalidInputError: unknown test, value outside the plausible range, a future or too old date.
    """
    await require_treating_doctor(session, actor, patient_id)
    existing = await session.scalar(
        select(DemoLabValue).where(
            DemoLabValue.entered_by == actor.user_id, DemoLabValue.submission_id == submission_id
        )
    )
    if existing is not None:
        if existing.patient_id != patient_id:
            raise InvalidStateError("Этот идентификатор отправки уже использован для другого пациента")
        return existing
    test = knowledge.lab_rules.test(code)
    if test is None:
        raise _problem("Неизвестный анализ", "code")
    if not test.min <= value <= test.max:
        raise _problem(f"{test.label}: значение вне диапазона {test.min:g}–{test.max:g} {test.unit}", "value")
    today = local_date(now, await patient_timezone(session, patient_id))
    if measured_on > today or measured_on < today - timedelta(days=MAX_AGE_DAYS):
        raise _problem("Дата анализа — не в будущем и не раньше чем 5 лет назад", "measured_on")
    row = DemoLabValue(
        id=uuid.uuid4(),
        created_at=now,
        patient_id=patient_id,
        code=code,
        value=value,
        measured_on=measured_on,
        entered_by=actor.user_id,
        submission_id=submission_id,
        removed_at=None,
        removed_by=None,
    )
    session.add(row)
    await session.flush()
    await audit.record(
        session,
        actor,
        "lab.demo_added",
        entity_type="demo_lab_value",
        entity_id=row.id,
        patient_id=patient_id,
        details={"code": code, "value": value, "measured_on": measured_on.isoformat()},
        now=now,
    )
    return row


async def remove(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, lab_id: uuid.UUID, *, now: datetime
) -> DemoLabValue:
    """The treating doctor removes a value (soft delete, audited)."""
    await require_treating_doctor(session, actor, patient_id)
    row = await session.get(DemoLabValue, lab_id, with_for_update=True)
    if row is None or row.patient_id != patient_id or row.removed_at is not None:
        raise NotFoundError("Значение не найдено")
    row.removed_at, row.removed_by = now, actor.user_id
    await audit.record(
        session,
        actor,
        "lab.demo_removed",
        entity_type="demo_lab_value",
        entity_id=row.id,
        patient_id=patient_id,
        details={"code": row.code},
        now=now,
    )
    return row


async def for_doctor(session: AsyncSession, actor: Actor, patient_id: uuid.UUID) -> list[DemoLabValue]:
    """All current values of the patient, newest measurement first."""
    await require_treating_doctor(session, actor, patient_id)
    rows = await session.scalars(
        select(DemoLabValue)
        .where(DemoLabValue.patient_id == patient_id, DemoLabValue.removed_at.is_(None))
        .order_by(DemoLabValue.measured_on.desc(), DemoLabValue.created_at.desc())
        .limit(500)
    )
    return list(rows.all())


async def values_for_checks(session: AsyncSession, patient_id: uuid.UUID) -> tuple[LabValue, ...]:
    """The patient's values for the basket checks (access already checked by the caller)."""
    rows = await session.execute(
        select(DemoLabValue.code, DemoLabValue.value, DemoLabValue.measured_on)
        .where(DemoLabValue.patient_id == patient_id, DemoLabValue.removed_at.is_(None))
        .order_by(DemoLabValue.measured_on, DemoLabValue.created_at)
    )
    return tuple(LabValue(code, value, measured_on) for code, value, measured_on in rows.all())
