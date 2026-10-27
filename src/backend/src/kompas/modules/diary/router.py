"""Endpoints: diary entries, doctor notes, freshness."""

import uuid
from datetime import date, datetime, timedelta
from typing import Any

from fastapi import APIRouter, Body, Query
from pydantic import BaseModel, Field

from kompas.api.deps import ActorDep, ClockDep, DoctorDep, KnowledgeDep, PatientDep, SessionDep
from kompas.core.clock import local_date
from kompas.core.errors import InvalidInputError, Problem
from kompas.json_input import parse_diary_entry
from kompas.modules.cases.service import patient_timezone
from kompas.modules.diary import service
from kompas.modules.diary.domain.entry import Sleep
from kompas.modules.diary.models import DiaryEntryRevision, DiaryEntryRow
from kompas.modules.knowledge.triage_rules import TriageLevel

router = APIRouter(tags=["diary"])


class EntryOut(BaseModel):
    """What the patient sees of an entry (their own answers; never triage or notes)."""

    entry_id: uuid.UUID
    revision_id: uuid.UUID
    revision: int
    day: date
    mood: int | None
    anxiety: int | None
    energy: int | None
    sleep: Sleep | None
    complaints: list[str]
    side_effects: list[str]
    self_harm_thoughts: bool | None
    text: str | None
    saved_at: datetime
    created_at: datetime | None = None
    schema_version: int = 1
    irritability: int | None = None
    sleep_hours: float | None = None
    unusual_experiences: bool | None = None

    @classmethod
    def of(cls, day: date, r: DiaryEntryRevision) -> "EntryOut":
        return cls(
            entry_id=r.entry_id,
            revision_id=r.id,
            revision=r.revision,
            day=day,
            schema_version=r.schema_version,
            irritability=r.irritability,
            sleep_hours=r.sleep_hours,
            unusual_experiences=r.unusual_experiences,
            mood=r.mood,
            anxiety=r.anxiety,
            energy=r.energy,
            sleep=r.sleep,
            complaints=r.complaints,
            side_effects=r.side_effects,
            self_harm_thoughts=r.self_harm_thoughts,
            text=r.text,
            saved_at=r.created_at,
        )


class TriageOut(BaseModel):
    level: TriageLevel
    label: str
    reasons: list[dict[str, Any]]
    critical_hits: list[dict[str, Any]]
    model_name: str | None
    model_unsure: bool
    model_unavailable: bool
    model_pending: bool


class NoteOut(BaseModel):
    note_id: uuid.UUID
    text: str
    created_at: datetime


class DoctorEntryOut(EntryOut):
    triage: TriageOut | None
    notes: list[NoteOut]


class NoteIn(BaseModel):
    text: str = Field(min_length=1)


class FreshnessOut(BaseModel):
    last_entry_on: date | None
    days_since_last: int | None
    missed_days: int
    window_days: int
    complete: bool


async def _days(session: SessionDep, revisions: list[DiaryEntryRevision]) -> dict[uuid.UUID, date]:
    days = {}
    for r in revisions:
        row = await session.get(DiaryEntryRow, r.entry_id)
        assert row is not None
        days[r.id] = row.day
    return days


async def _range(
    session: SessionDep, patient_id: uuid.UUID, now: datetime, since: date | None, until: date | None
) -> tuple[date, date]:
    today = local_date(now, await patient_timezone(session, patient_id))
    return since or today - timedelta(days=30), until or today


@router.post("/me/diary", status_code=201)
async def post_entry(
    actor: PatientDep,
    session: SessionDep,
    knowledge: KnowledgeDep,
    clock: ClockDep,
    body: dict[str, Any] = Body(...),
) -> EntryOut:
    """POST /me/diary — patient (with submission_id); response has no triage."""
    raw_id = body.get("submission_id")
    try:
        submission_id = uuid.UUID(str(raw_id))
    except ValueError:
        raise InvalidInputError(
            "Нужен submission_id (UUID)", (Problem("invalid_input", "Нет submission_id", "submission_id"),)
        ) from None
    entry = parse_diary_entry(body, "entry")
    revision = await service.save_entry(session, actor, knowledge, entry, submission_id=submission_id, now=clock.now())
    row = await session.get(DiaryEntryRow, revision.entry_id)
    assert row is not None
    return EntryOut.of(row.day, revision).model_copy(update={"created_at": row.created_at})


@router.get("/me/diary")
async def get_my_entries(
    actor: PatientDep,
    session: SessionDep,
    clock: ClockDep,
    since: date | None = Query(None),
    until: date | None = Query(None),
) -> list[EntryOut]:
    """GET /me/diary?since&until — patient."""
    assert actor.patient_id is not None
    start, end = await _range(session, actor.patient_id, clock.now(), since, until)
    revisions = await service.my_entries(session, actor, since=start, until=end)
    days = await _days(session, revisions)
    out = []
    for r in revisions:
        row = await session.get(DiaryEntryRow, r.entry_id)
        assert row is not None
        out.append(EntryOut.of(days[r.id], r).model_copy(update={"created_at": row.created_at}))
    return out


@router.get("/patients/{patient_id}/diary")
async def get_entries(
    patient_id: uuid.UUID,
    actor: DoctorDep,
    session: SessionDep,
    clock: ClockDep,
    since: date | None = Query(None),
    until: date | None = Query(None),
) -> list[DoctorEntryOut]:
    """GET /patients/{id}/diary?since&until — doctor, with triage and notes."""
    start, end = await _range(session, patient_id, clock.now(), since, until)
    views = await service.doctor_entries(session, actor, patient_id, since=start, until=end)
    days = await _days(session, [v.revision for v in views])
    out = []
    for v in views:
        base = EntryOut.of(days[v.revision.id], v.revision)
        triage = None
        if v.triage is not None:
            t = v.triage
            triage = TriageOut(
                level=t.level,
                label=t.level.label,
                reasons=t.reasons,
                critical_hits=t.critical_hits,
                model_name=t.model_name,
                model_unsure=t.model_unsure,
                model_unavailable=t.model_unavailable,
                model_pending=t.model_pending,
            )
        notes = [NoteOut(note_id=n.id, text=n.text, created_at=n.created_at) for n in v.notes]
        out.append(DoctorEntryOut(**base.model_dump(), triage=triage, notes=notes))
    return out


@router.post("/diary/{entry_id}/notes", status_code=201)
async def post_note(
    entry_id: uuid.UUID, body: NoteIn, actor: DoctorDep, session: SessionDep, clock: ClockDep
) -> NoteOut:
    """POST /diary/{entry_id}/notes — doctor's private note."""
    note = await service.add_note(session, actor, entry_id, body.text, now=clock.now())
    return NoteOut(note_id=note.id, text=note.text, created_at=note.created_at)


@router.get("/patients/{patient_id}/freshness")
async def get_freshness(
    patient_id: uuid.UUID,
    actor: ActorDep,
    session: SessionDep,
    clock: ClockDep,
    window_days: int = Query(7, ge=1, le=90),
) -> FreshnessOut:
    """GET /patients/{id}/freshness."""
    today = local_date(clock.now(), await patient_timezone(session, patient_id))
    f = await service.freshness(session, actor, patient_id, today=today, window_days=window_days)
    return FreshnessOut(
        last_entry_on=f.last_entry_on,
        days_since_last=f.days_since_last,
        missed_days=f.missed_days,
        window_days=f.window_days,
        complete=f.complete,
    )
