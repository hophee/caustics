"""Diary use cases.

Saving (patient): validate → new revision (day = the patient's local date via the profile time zone)
→ triage with rules + trend (old diary) or v2 rules + streaks (diary v2) + keyword model now →
critical hits raise alerts (dedup per revision and rule) in the same transaction → outbox
``diary.model_triage`` with the revision id. Urgent alerts carry a scenario (D-132): a structured
answer → ``urgent_scenarios.diary_answers``, free text → ``urgent_scenarios.diary_text``. The worker's
verdict can only raise the level (D-033); stale verdicts (older revision) are dropped; a rise to
URGENT raises an alert.
"""

import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from kompas.core import outbox
from kompas.core.actor import Actor
from kompas.core.clock import local_date
from kompas.core.errors import InvalidInputError, NotFoundError, PermissionDeniedError, Problem
from kompas.core.jsonable import to_jsonable
from kompas.modules.audit import service as audit
from kompas.modules.cases.models import MedicalCase
from kompas.modules.cases.service import active_case, patient_timezone, require_treating_doctor
from kompas.modules.diary.domain.entry import DiaryEntry
from kompas.modules.diary.models import DiaryEntryRevision, DiaryEntryRow, DiaryNote, DiaryTriageRow
from kompas.modules.identity.service import ensure_patient_access
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.triage_rules import TriageLevel, UrgentScenario
from kompas.modules.monitoring import service as monitoring
from kompas.modules.monitoring.domain.freshness import Freshness, diary_freshness
from kompas.modules.monitoring.domain.text_model import KeywordModel, ModelVerdict
from kompas.modules.monitoring.domain.triage import (
    PatientContext,
    ReasonSource,
    TriageResult,
    combine,
    evaluate_entry,
    evaluate_entry_v2,
    evaluate_streaks,
    evaluate_trend,
    model_reason,
    streak_window,
)
from kompas.modules.monitoring.models import AlertLevel
from kompas.modules.referrals import service as referrals
from kompas.modules.therapy.service import on_antidepressant

TOPIC = "diary.model_triage"


def _domain(day: date, revision: DiaryEntryRevision) -> DiaryEntry:
    return DiaryEntry(
        day=day,
        mood=revision.mood,
        anxiety=revision.anxiety,
        energy=revision.energy,
        sleep=revision.sleep,
        complaints=frozenset(revision.complaints),
        side_effects=tuple(revision.side_effects),
        self_harm_thoughts=revision.self_harm_thoughts,
        text=revision.text,
        schema_version=revision.schema_version,
        irritability=revision.irritability,
        sleep_hours=revision.sleep_hours,
        unusual_experiences=revision.unusual_experiences,
    )


async def _latest(
    session: AsyncSession, patient_id: uuid.UUID, since: date, until: date
) -> list[tuple[DiaryEntryRow, DiaryEntryRevision]]:
    rows = await session.execute(
        select(DiaryEntryRow, DiaryEntryRevision)
        .join(
            DiaryEntryRevision,
            (DiaryEntryRevision.entry_id == DiaryEntryRow.id)
            & (DiaryEntryRevision.revision == DiaryEntryRow.current_revision),
        )
        .join(MedicalCase, MedicalCase.id == DiaryEntryRow.case_id)
        .where(MedicalCase.patient_id == patient_id, DiaryEntryRow.day >= since, DiaryEntryRow.day <= until)
        .order_by(DiaryEntryRow.day)
    )
    return [(entry, revision) for entry, revision in rows.all()]


async def _alert(
    session: AsyncSession,
    knowledge: Knowledge,
    case: MedicalCase,
    revision_id: uuid.UUID,
    *,
    key: str,
    source: str,
    reason: str,
    details: dict[str, Any],
    scenario: UrgentScenario,
    now: datetime,
) -> None:
    await monitoring.raise_alert(
        session,
        patient_id=case.patient_id,
        case_id=case.id,
        level=AlertLevel.URGENT,
        scenario=scenario,
        source=source,
        source_id=revision_id,
        reason=reason,
        details=details,
        dedup_key=f"diary:{revision_id}:{key}",
        knowledge_version=knowledge.version,
        now=now,
    )


@dataclass(frozen=True, slots=True)
class DoctorEntryView:
    revision: DiaryEntryRevision
    triage: DiaryTriageRow | None
    notes: tuple[DiaryNote, ...]


async def save_entry(
    session: AsyncSession,
    actor: Actor,
    knowledge: Knowledge,
    entry: DiaryEntry,
    *,
    submission_id: uuid.UUID,
    now: datetime,
) -> DiaryEntryRevision:
    """Patient saves an entry (day ≤ local today). Upserts the day's ``diary_entry`` and locks it
    before assigning ``revision = current_revision + 1`` (concurrent saves serialise). Returns the
    revision only (patients never see triage). Repeated ``submission_id`` (scoped to the patient) →
    the stored revision. Audited (no free text in audit).

    Raises:
        InvalidInputError: ``entry.problems`` not empty or a future day.
    """
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Дневник заполняет пациент")
    existing = await session.scalar(
        select(DiaryEntryRevision).where(
            DiaryEntryRevision.patient_id == actor.patient_id, DiaryEntryRevision.submission_id == submission_id
        )
    )
    if existing is not None:
        return existing
    problems = entry.problems(knowledge.complaints.codes())
    if problems:
        raise InvalidInputError(
            "Запись дневника некорректна", tuple(Problem("invalid_input", p, "entry") for p in problems)
        )
    tz = await patient_timezone(session, actor.patient_id)
    if entry.day > local_date(now, tz):
        raise InvalidInputError(
            "Нельзя заполнить дневник на будущий день", (Problem("invalid_input", "Будущий день", "day"),)
        )
    case = await active_case(session, actor, actor.patient_id)

    await session.execute(
        insert(DiaryEntryRow)
        .values(id=uuid.uuid4(), created_at=now, case_id=case.id, day=entry.day, current_revision=0)
        .on_conflict_do_nothing(constraint="uq_diary_entry_case_day")
    )
    row = await session.scalar(
        select(DiaryEntryRow)
        .where(DiaryEntryRow.case_id == case.id, DiaryEntryRow.day == entry.day)
        .with_for_update()
        .execution_options(populate_existing=True)
    )
    assert row is not None
    if row.current_revision and now - row.created_at > timedelta(hours=24):
        raise PermissionDeniedError("Записи старше суток нельзя редактировать")
    row.current_revision += 1
    revision = DiaryEntryRevision(
        id=uuid.uuid4(),
        created_at=now,
        entry_id=row.id,
        patient_id=actor.patient_id,
        revision=row.current_revision,
        submission_id=submission_id,
        schema_version=entry.schema_version,
        irritability=entry.irritability,
        sleep_hours=entry.sleep_hours,
        unusual_experiences=entry.unusual_experiences,
        mood=entry.mood,
        anxiety=entry.anxiety,
        energy=entry.energy,
        sleep=entry.sleep,
        complaints=sorted(entry.complaints),
        side_effects=list(entry.side_effects),
        self_harm_thoughts=entry.self_harm_thoughts,
        text=entry.text,
    )
    session.add(revision)
    await session.flush()

    rules = knowledge.triage_rules
    context = PatientContext(on_antidepressant=await on_antidepressant(session, case.id, knowledge))
    reasons, hits = evaluate_entry(entry, rules, knowledge.complaints, context)
    if entry.schema_version == 2:
        reasons += evaluate_entry_v2(entry, rules)
        window = streak_window(rules)
        nearby = await _latest(
            session, actor.patient_id, entry.day - timedelta(days=window), entry.day + timedelta(days=window)
        )
        reasons += evaluate_streaks(entry, [_domain(e.day, r) for e, r in nearby], rules)
    else:
        since = entry.day - timedelta(days=rules.mood_drop_days)
        history = [
            _domain(e.day, r) for e, r in await _latest(session, actor.patient_id, since, entry.day - timedelta(days=1))
        ]
        reasons += evaluate_trend([*history, entry], rules)
    unsure = False
    if entry.text:
        reason, unsure = model_reason(KeywordModel(rules.keywords).classify(entry.text), rules)
        reasons.append(reason)
    triage = combine(reasons, hits, model_unsure=unsure)
    session.add(
        DiaryTriageRow(
            id=uuid.uuid4(),
            created_at=now,
            revision_id=revision.id,
            level=triage.level,
            reasons=[to_jsonable(r) for r in triage.reasons],
            critical_hits=[to_jsonable(h) for h in triage.critical_hits],
            knowledge_version=knowledge.version,
            model_name=None,
            model_version=None,
            model_unsure=triage.model_unsure,
            model_unavailable=False,
            model_pending=bool(entry.text),
            updated_at=now,
        )
    )
    for hit in triage.critical_hits:
        await _alert(
            session,
            knowledge,
            case,
            revision.id,
            key=hit.code,
            source="diary",
            reason=f"Дневник: {hit.message}",
            details=to_jsonable(hit),
            scenario=rules.urgent_scenarios.diary_answers,
            now=now,
        )
    if triage.level is TriageLevel.URGENT and not triage.critical_hits:
        await _alert(
            session,
            knowledge,
            case,
            revision.id,
            key="urgent",
            source="diary",
            reason="Дневник: запись оценена как «Срочно»",
            details={"reasons": [r.code for r in triage.reasons]},
            scenario=_scenario(triage, knowledge),
            now=now,
        )
    if triage.level is TriageLevel.URGENT:
        await referrals.recommend_from_rules(
            session,
            knowledge,
            patient_id=case.patient_id,
            case_id=case.id,
            source="diary",
            source_id=revision.id,
            diary_urgent=True,
            now=now,
        )
    if entry.text:
        await outbox.enqueue(session, TOPIC, model_payload(revision.id), now=now)
    await audit.record(
        session,
        actor,
        "diary.saved",
        entity_type="diary_entry_revision",
        entity_id=revision.id,
        patient_id=actor.patient_id,
        details={"day": entry.day.isoformat(), "revision": revision.revision},
        now=now,
    )
    return revision


def _scenario(triage: TriageResult, knowledge: Knowledge) -> UrgentScenario:
    """Urgent without a critical hit: a structured rule (old-diary complaint) → ``diary_answers``;
    only the text said «Срочно» → ``diary_text``."""
    scenarios = knowledge.triage_rules.urgent_scenarios
    structured = any(r.level is TriageLevel.URGENT and r.source is not ReasonSource.MODEL for r in triage.reasons)
    return scenarios.diary_answers if structured else scenarios.diary_text


def model_payload(revision_id: uuid.UUID) -> dict[str, Any]:
    """Outbox payload for ``diary.model_triage``: the revision id only. The patient's text stays in
    the diary tables and is read by the worker, never copied into the long-lived outbox."""
    return {"revision_id": str(revision_id)}


async def apply_model_verdict(
    session: AsyncSession, knowledge: Knowledge, revision_id: uuid.UUID, verdict: ModelVerdict | None, *, now: datetime
) -> None:
    """Merge a model verdict computed **outside** the transaction (worker). None = model failed →
    ``model_unavailable=True``, keyword verdict kept. Dropped if the entry has a newer revision."""
    triage = await session.scalar(
        select(DiaryTriageRow).where(DiaryTriageRow.revision_id == revision_id).with_for_update()
    )
    revision = await session.get(DiaryEntryRevision, revision_id)
    if triage is None or revision is None:
        return
    entry = await session.get(DiaryEntryRow, revision.entry_id)
    assert entry is not None
    triage.model_pending = False
    triage.updated_at = now
    if entry.current_revision != revision.revision:
        return  # stale: the patient saved a newer version, which has its own model job
    if verdict is None:
        triage.model_unavailable = True
        return
    rules = knowledge.triage_rules
    reason, unsure = model_reason(verdict, rules)
    triage.model_name = verdict.model_name
    triage.model_version = verdict.model_version
    triage.model_unsure = triage.model_unsure or unsure
    keyword_codes = {"model_unsure", "model_stable", "model_attention", "model_urgent"}
    kept = [r for r in triage.reasons if not (r.get("source") == "model" and r.get("code") in keyword_codes)]
    triage.reasons = [to_jsonable(reason), *kept]
    previous = triage.level
    if reason.level.rank > previous.rank:  # the model can only raise the level (D-033)
        triage.level = reason.level
    if triage.level is TriageLevel.URGENT and previous is not TriageLevel.URGENT:
        case = await session.get(MedicalCase, entry.case_id)
        assert case is not None
        await _alert(
            session,
            knowledge,
            case,
            revision.id,
            key="model_urgent",
            source="diary_model",
            reason="Дневник: модель оценила текст как «Срочно»",
            details={"model": verdict.model_name},
            scenario=rules.urgent_scenarios.diary_text,
            now=now,
        )
        await referrals.recommend_from_rules(
            session,
            knowledge,
            patient_id=case.patient_id,
            case_id=case.id,
            source="diary",
            source_id=revision.id,
            diary_urgent=True,
            now=now,
        )


async def doctor_entries(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, *, since: date, until: date
) -> list[DoctorEntryView]:
    """Latest revisions with triage and notes (doctor)."""
    await require_treating_doctor(session, actor, patient_id)
    pairs = await _latest(session, patient_id, since, until)
    revision_ids = [r.id for _, r in pairs]
    entry_ids = [e.id for e, _ in pairs]
    triages = {
        t.revision_id: t
        for t in (await session.scalars(select(DiaryTriageRow).where(DiaryTriageRow.revision_id.in_(revision_ids))))
    }
    notes: dict[uuid.UUID, list[DiaryNote]] = {}
    for note in await session.scalars(
        select(DiaryNote).where(DiaryNote.entry_id.in_(entry_ids)).order_by(DiaryNote.created_at)
    ):
        notes.setdefault(note.entry_id, []).append(note)
    return [DoctorEntryView(r, triages.get(r.id), tuple(notes.get(e.id, ()))) for e, r in pairs]


async def my_entries(session: AsyncSession, actor: Actor, *, since: date, until: date) -> list[DiaryEntryRevision]:
    """Patient: own latest revisions only (no triage, no notes)."""
    if not actor.is_patient or actor.patient_id is None:
        raise PermissionDeniedError("Действие доступно только пациенту")
    return [r for _, r in await _latest(session, actor.patient_id, since, until)]


async def add_note(session: AsyncSession, actor: Actor, entry_id: uuid.UUID, text: str, *, now: datetime) -> DiaryNote:
    """Treating doctor adds a private note; audited."""
    entry = await session.get(DiaryEntryRow, entry_id)
    case = await session.get(MedicalCase, entry.case_id) if entry else None
    if entry is None or case is None:
        raise NotFoundError("Запись не найдена")
    try:
        await require_treating_doctor(session, actor, case.patient_id)
    except NotFoundError:
        raise NotFoundError("Запись не найдена") from None
    assert actor.doctor_id is not None
    if not text.strip():
        raise InvalidInputError("Заметка пустая", (Problem("invalid_input", "Пустая заметка", "text"),))
    note = DiaryNote(id=uuid.uuid4(), created_at=now, entry_id=entry.id, doctor_id=actor.doctor_id, text=text.strip())
    session.add(note)
    await session.flush()
    await audit.record(
        session,
        actor,
        "diary.note_added",
        entity_type="diary_note",
        entity_id=note.id,
        patient_id=case.patient_id,
        details={},
        now=now,
    )
    return note


async def freshness(
    session: AsyncSession, actor: Actor, patient_id: uuid.UUID, *, today: date, window_days: int
) -> Freshness:
    """Diary data freshness for the doctor's list (A12)."""
    await ensure_patient_access(session, actor, patient_id)
    tz = await patient_timezone(session, patient_id)
    try:
        case = await active_case(session, actor, patient_id)
        since = local_date(case.created_at, tz)
    except NotFoundError:
        since = today
    days = [e.day for e, _ in await _latest(session, patient_id, today - timedelta(days=window_days * 4), today)]
    return diary_freshness(days, today, window_days, since)
