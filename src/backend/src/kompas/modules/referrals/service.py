"""Psychiatrist referral use cases (spec §10, F8; rules = open question B02).

Human-in-the-loop (M2): rules and doctors create *recommendations*; only the treating doctor decides
(accept/decline, reason required) and issues a referral. The patient sees issued referrals only.
"""

import uuid
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core.actor import Actor
from kompas.core.errors import InvalidInputError, InvalidStateError, NotFoundError, PermissionDeniedError, Problem
from kompas.core.icd import IcdCode
from kompas.modules.audit import service as audit
from kompas.modules.cases.models import CaseStatus, MedicalCase
from kompas.modules.cases.service import main_diagnosis, require_treating_doctor
from kompas.modules.identity.models import Doctor, Patient
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.care_rules import ReferralCategory
from kompas.modules.notifications import service as notifications
from kompas.modules.referrals.models import (
    RecommendationStatus,
    Referral,
    ReferralKind,
    ReferralRecommendation,
    ReferralStatus,
    ReferralUrgency,
)


def _reason(text: str, pointer: str) -> str:
    value = text.strip()
    if len(value) < 3:
        raise InvalidInputError("Укажите основание", (Problem("reason_required", "Нужно основание", pointer),))
    return value


async def _active_case(session: AsyncSession, patient_id: uuid.UUID) -> MedicalCase:
    case = await session.scalar(
        select(MedicalCase).where(
            MedicalCase.patient_id == patient_id, MedicalCase.status == CaseStatus.ACTIVE, MedicalCase.demo.is_(False)
        )
    )
    if case is None:
        raise InvalidStateError("У пациента нет активного случая")
    return case


async def _notify_doctor(
    session: AsyncSession, patient_id: uuid.UUID, rec: ReferralRecommendation, now: datetime
) -> None:
    user_id = await session.scalar(
        select(Doctor.user_id).join(Patient, Patient.treating_doctor_id == Doctor.id).where(Patient.id == patient_id)
    )
    if user_id is not None:
        await notifications.notify(
            session,
            user_id,
            "referral_recommendation",
            {"recommendation_id": str(rec.id), "patient_id": str(patient_id), "category": rec.category.value},
            now=now,
            patient_id=patient_id,
        )


async def recommend_from_rules(
    session: AsyncSession,
    knowledge: Knowledge,
    *,
    patient_id: uuid.UUID,
    case_id: uuid.UUID,
    source: str,
    source_id: uuid.UUID,
    scale: str | None = None,
    level: int | None = None,
    critical: bool = False,
    diary_urgent: bool = False,
    now: datetime,
) -> list[ReferralRecommendation]:
    """Evaluate ``rules/referrals.yaml`` for one event (a scored response or a triaged diary entry) and
    create the matching recommendations (idempotent per rule and event). With the shipped empty rule
    list this does nothing (B02 open)."""
    created = []
    for rule in knowledge.referral_rules.rules:
        hit = (
            (
                rule.kind == "scale_level"
                and scale == rule.scale
                and level is not None
                and rule.min_level is not None
                and level >= rule.min_level
            )
            or (rule.kind == "critical_answer" and scale == rule.scale and critical)
            or (rule.kind == "diary_urgent" and diary_urgent)
        )
        if not hit:
            continue
        rec_id = await session.scalar(
            insert(ReferralRecommendation)
            .values(
                id=uuid.uuid4(),
                created_at=now,
                patient_id=patient_id,
                case_id=case_id,
                category=rule.category,
                reason=rule.reason,
                rule_code=rule.code,
                source=source,
                source_id=source_id,
                created_by=None,
                submission_id=None,
                dedup_key=f"{rule.code}:{source_id}",
                status=RecommendationStatus.OPEN,
                decided_by=None,
                decided_at=None,
                decision_reason=None,
            )
            .on_conflict_do_nothing(index_elements=[ReferralRecommendation.dedup_key])
            .returning(ReferralRecommendation.id)
        )
        if rec_id is None:
            continue
        rec = await session.get(ReferralRecommendation, rec_id)
        assert rec is not None
        await _notify_doctor(session, patient_id, rec, now)
        created.append(rec)
    return created


async def recommend(
    session: AsyncSession,
    actor: Actor,
    patient_id: uuid.UUID,
    *,
    category: ReferralCategory,
    reason: str,
    submission_id: uuid.UUID,
    now: datetime,
) -> ReferralRecommendation:
    """The treating doctor records a recommendation by hand (e.g. while B02 rules are not set).
    Repeating ``submission_id`` returns the stored recommendation."""
    await require_treating_doctor(session, actor, patient_id)
    existing = await session.scalar(
        select(ReferralRecommendation).where(
            ReferralRecommendation.created_by == actor.user_id, ReferralRecommendation.submission_id == submission_id
        )
    )
    if existing is not None:
        return existing
    case = await _active_case(session, patient_id)
    rec = ReferralRecommendation(
        id=uuid.uuid4(),
        created_at=now,
        patient_id=patient_id,
        case_id=case.id,
        category=category,
        reason=_reason(reason, "reason"),
        rule_code=None,
        source="doctor",
        source_id=None,
        created_by=actor.user_id,
        submission_id=submission_id,
        dedup_key=None,
        status=RecommendationStatus.OPEN,
        decided_by=None,
        decided_at=None,
        decision_reason=None,
    )
    session.add(rec)
    await session.flush()
    await audit.record(
        session,
        actor,
        "referral.recommended",
        entity_type="referral_recommendation",
        entity_id=rec.id,
        patient_id=patient_id,
        details={"category": category.value},
        now=now,
    )
    return rec


async def _own_recommendation(session: AsyncSession, actor: Actor, rec_id: uuid.UUID) -> ReferralRecommendation:
    rec = await session.get(ReferralRecommendation, rec_id)
    if rec is None:
        raise NotFoundError("Рекомендация не найдена")
    try:
        await require_treating_doctor(session, actor, rec.patient_id)
    except NotFoundError:
        raise NotFoundError("Рекомендация не найдена") from None
    locked = await session.get(ReferralRecommendation, rec_id, with_for_update=True, populate_existing=True)
    assert locked is not None
    return locked


async def decide(
    session: AsyncSession, actor: Actor, rec_id: uuid.UUID, *, accept: bool, reason: str, now: datetime
) -> ReferralRecommendation:
    """The doctor accepts or declines an open recommendation; the reason is required either way.

    Raises:
        InvalidStateError: already decided.
    """
    rec = await _own_recommendation(session, actor, rec_id)
    if rec.status is not RecommendationStatus.OPEN:
        raise InvalidStateError("Решение по рекомендации уже принято")
    rec.status = RecommendationStatus.ACCEPTED if accept else RecommendationStatus.DECLINED
    rec.decided_by, rec.decided_at, rec.decision_reason = actor.user_id, now, _reason(reason, "reason")
    await audit.record(
        session,
        actor,
        "referral.recommendation_decided",
        entity_type="referral_recommendation",
        entity_id=rec.id,
        patient_id=rec.patient_id,
        details={"accepted": accept, "category": rec.category.value},
        now=now,
    )
    return rec


async def issue(
    session: AsyncSession,
    actor: Actor,
    patient_id: uuid.UUID,
    *,
    kind: ReferralKind,
    specialist: str,
    urgency: ReferralUrgency,
    clinical_reason: str,
    valid_until: date,
    category: ReferralCategory | None,
    recommendation_id: uuid.UUID | None,
    submission_id: uuid.UUID,
    today: date,
    now: datetime,
) -> Referral:
    """The doctor issues a referral (optionally from a recommendation, which is then accepted; one
    referral per recommendation). The diagnosis is the case's main diagnosis. The patient is notified.
    Repeating ``submission_id`` returns the stored referral.

    Raises:
        InvalidInputError: validity in the past, missing reason. InvalidStateError: the recommendation
        was declined, already has a referral, or belongs to another patient or an earlier case.
    """
    await require_treating_doctor(session, actor, patient_id)
    assert actor.doctor_id is not None
    existing = await session.scalar(
        select(Referral).where(Referral.issued_by == actor.doctor_id, Referral.submission_id == submission_id)
    )
    if existing is not None:
        return existing
    case = await _active_case(session, patient_id)
    if valid_until < today:
        raise InvalidInputError("Срок действия в прошлом", (Problem("invalid_input", "Срок в прошлом", "valid_until"),))
    reason = _reason(clinical_reason, "clinical_reason")
    if recommendation_id is not None:
        rec = await _own_recommendation(session, actor, recommendation_id)
        if rec.patient_id != patient_id or rec.case_id != case.id or rec.status is RecommendationStatus.DECLINED:
            raise InvalidStateError("Рекомендация отклонена или относится к другому пациенту или случаю")
        if await session.scalar(select(Referral.id).where(Referral.recommendation_id == rec.id)) is not None:
            raise InvalidStateError("По этой рекомендации направление уже оформлено")
        if rec.status is RecommendationStatus.OPEN:
            rec.status, rec.decided_by, rec.decided_at = RecommendationStatus.ACCEPTED, actor.user_id, now
            rec.decision_reason = "Оформлено направление"
        category = category or rec.category
    main = await main_diagnosis(session, case.id)
    referral = Referral(
        id=uuid.uuid4(),
        created_at=now,
        patient_id=patient_id,
        case_id=case.id,
        recommendation_id=recommendation_id,
        submission_id=submission_id,
        kind=kind,
        specialist=specialist.strip() or "психиатр",
        category=category,
        urgency=urgency,
        clinical_reason=reason,
        diagnosis_code=IcdCode(main.icd_code, main.icd_original).code if main else None,
        valid_until=valid_until,
        issued_by=actor.doctor_id,
        status=ReferralStatus.ISSUED,
        cancelled_at=None,
        cancel_reason=None,
    )
    session.add(referral)
    await session.flush()
    await audit.record(
        session,
        actor,
        "referral.issued",
        entity_type="referral",
        entity_id=referral.id,
        patient_id=patient_id,
        details={"kind": kind.value, "category": category.value if category else None, "urgency": urgency.value},
        now=now,
    )
    user_id = await session.scalar(select(Patient.user_id).where(Patient.id == patient_id))
    if user_id is not None:
        await notifications.notify(
            session, user_id, "referral", {"referral_id": str(referral.id)}, now=now, patient_id=patient_id
        )
    return referral


async def cancel(
    session: AsyncSession, actor: Actor, referral_id: uuid.UUID, *, reason: str, now: datetime
) -> Referral:
    """The doctor cancels an issued referral (reason required)."""
    referral = await session.get(Referral, referral_id)
    if referral is None:
        raise NotFoundError("Направление не найдено")
    try:
        await require_treating_doctor(session, actor, referral.patient_id)
    except NotFoundError:
        raise NotFoundError("Направление не найдено") from None
    if referral.status is ReferralStatus.CANCELLED:
        raise InvalidStateError("Направление уже отменено")
    referral.status, referral.cancelled_at, referral.cancel_reason = (
        ReferralStatus.CANCELLED,
        now,
        _reason(reason, "reason"),
    )
    await audit.record(
        session,
        actor,
        "referral.cancelled",
        entity_type="referral",
        entity_id=referral.id,
        patient_id=referral.patient_id,
        details={},
        now=now,
    )
    return referral


async def for_doctor(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID
) -> tuple[list[ReferralRecommendation], list[Referral]]:
    """All recommendations (open first) and referrals of the patient."""
    await require_treating_doctor(session, actor, patient_id)
    recs = list(
        (
            await session.scalars(
                select(ReferralRecommendation)
                .where(ReferralRecommendation.patient_id == patient_id)
                .order_by(
                    (ReferralRecommendation.status == RecommendationStatus.OPEN).desc(),
                    ReferralRecommendation.created_at.desc(),
                )
            )
        ).all()
    )
    refs = list(
        (
            await session.scalars(
                select(Referral).where(Referral.patient_id == patient_id).order_by(Referral.created_at.desc())
            )
        ).all()
    )
    return recs, refs


async def for_patient(session: AsyncSession, actor: Actor) -> list[Referral]:
    """The patient's issued (and cancelled) referrals — never recommendations."""
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    return list(
        (
            await session.scalars(
                select(Referral).where(Referral.patient_id == actor.patient_id).order_by(Referral.created_at.desc())
            )
        ).all()
    )
