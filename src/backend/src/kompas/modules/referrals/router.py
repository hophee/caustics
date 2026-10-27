"""Endpoints: referral recommendations and referrals (doctor), issued referrals (patient)."""

import uuid
from datetime import date, datetime

from fastapi import APIRouter
from pydantic import BaseModel, Field

from kompas.api.deps import ClockDep, DoctorDep, PatientDep, SessionDep
from kompas.core.clock import local_date
from kompas.modules.cases.service import patient_timezone
from kompas.modules.knowledge.care_rules import ReferralCategory
from kompas.modules.referrals import service
from kompas.modules.referrals.models import (
    RecommendationStatus,
    Referral,
    ReferralKind,
    ReferralRecommendation,
    ReferralStatus,
    ReferralUrgency,
)

router = APIRouter(tags=["referrals"])


class RecommendationIn(BaseModel):
    category: ReferralCategory
    reason: str = Field(min_length=3)
    submission_id: uuid.UUID


class DecisionIn(BaseModel):
    accept: bool
    reason: str = Field(min_length=3)


class ReferralIn(BaseModel):
    kind: ReferralKind = ReferralKind.CONSULTATION
    specialist: str = "психиатр"
    urgency: ReferralUrgency = ReferralUrgency.PLANNED
    category: ReferralCategory | None = None
    clinical_reason: str = Field(min_length=3)
    valid_until: date
    recommendation_id: uuid.UUID | None = None
    submission_id: uuid.UUID


class CancelIn(BaseModel):
    reason: str = Field(min_length=3)


class RecommendationOut(BaseModel):
    recommendation_id: uuid.UUID
    category: ReferralCategory
    category_label: str
    reason: str
    rule_code: str | None
    source: str | None
    status: RecommendationStatus
    created_at: datetime
    decided_at: datetime | None
    decision_reason: str | None

    @classmethod
    def of(cls, r: ReferralRecommendation) -> "RecommendationOut":
        return cls(
            recommendation_id=r.id,
            category=r.category,
            category_label=r.category.label,
            reason=r.reason,
            rule_code=r.rule_code,
            source=r.source,
            status=r.status,
            created_at=r.created_at,
            decided_at=r.decided_at,
            decision_reason=r.decision_reason,
        )


class PatientReferralOut(BaseModel):
    """What the patient sees: no internal reasoning."""

    referral_id: uuid.UUID
    kind: ReferralKind
    specialist: str
    category_label: str | None
    urgency: ReferralUrgency
    valid_until: date
    issued_at: datetime
    status: ReferralStatus

    @classmethod
    def of(cls, r: Referral) -> "PatientReferralOut":
        return cls(
            referral_id=r.id,
            kind=r.kind,
            specialist=r.specialist,
            category_label=r.category.label if r.category else None,
            urgency=r.urgency,
            valid_until=r.valid_until,
            issued_at=r.created_at,
            status=r.status,
        )


class ReferralOut(PatientReferralOut):
    category: ReferralCategory | None
    clinical_reason: str
    diagnosis_code: str | None
    recommendation_id: uuid.UUID | None
    cancel_reason: str | None

    @classmethod
    def of(cls, r: Referral) -> "ReferralOut":
        return cls(
            **PatientReferralOut.of(r).model_dump(),
            category=r.category,
            clinical_reason=r.clinical_reason,
            diagnosis_code=r.diagnosis_code,
            recommendation_id=r.recommendation_id,
            cancel_reason=r.cancel_reason,
        )


class DoctorReferralsOut(BaseModel):
    recommendations: list[RecommendationOut]
    referrals: list[ReferralOut]


@router.get("/patients/{patient_id}/referrals")
async def get_referrals(patient_id: uuid.UUID, actor: DoctorDep, session: SessionDep) -> DoctorReferralsOut:
    """GET /patients/{id}/referrals — recommendations (open first) and referrals."""
    recs, refs = await service.for_doctor(session, actor, patient_id)
    return DoctorReferralsOut(
        recommendations=[RecommendationOut.of(r) for r in recs], referrals=[ReferralOut.of(r) for r in refs]
    )


@router.post("/patients/{patient_id}/referral-recommendations", status_code=201)
async def post_recommendation(
    patient_id: uuid.UUID, body: RecommendationIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> RecommendationOut:
    """POST — the doctor records «обязательно / желательно» by hand."""
    rec = await service.recommend(
        session,
        actor,
        patient_id,
        category=body.category,
        reason=body.reason,
        submission_id=body.submission_id,
        now=clock.now(),
    )
    return RecommendationOut.of(rec)


@router.post("/referral-recommendations/{recommendation_id}/decision")
async def post_decision(
    recommendation_id: uuid.UUID, body: DecisionIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> RecommendationOut:
    """POST — accept or decline with a reason."""
    rec = await service.decide(
        session, actor, recommendation_id, accept=body.accept, reason=body.reason, now=clock.now()
    )
    return RecommendationOut.of(rec)


@router.post("/patients/{patient_id}/referrals", status_code=201)
async def post_referral(
    patient_id: uuid.UUID, body: ReferralIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> ReferralOut:
    """POST — issue a referral (optionally from a recommendation)."""
    now = clock.now()
    today = local_date(now, await patient_timezone(session, patient_id))
    referral = await service.issue(
        session,
        actor,
        patient_id,
        kind=body.kind,
        specialist=body.specialist,
        urgency=body.urgency,
        clinical_reason=body.clinical_reason,
        valid_until=body.valid_until,
        category=body.category,
        recommendation_id=body.recommendation_id,
        submission_id=body.submission_id,
        today=today,
        now=now,
    )
    return ReferralOut.of(referral)


@router.post("/referrals/{referral_id}/cancel")
async def post_cancel(
    referral_id: uuid.UUID, body: CancelIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> ReferralOut:
    """POST — cancel an issued referral (reason required)."""
    return ReferralOut.of(await service.cancel(session, actor, referral_id, reason=body.reason, now=clock.now()))


@router.get("/me/referrals")
async def get_my_referrals(actor: PatientDep, session: SessionDep) -> list[PatientReferralOut]:
    """GET /me/referrals — the patient's issued referrals."""
    return [PatientReferralOut.of(r) for r in await service.for_patient(session, actor)]
