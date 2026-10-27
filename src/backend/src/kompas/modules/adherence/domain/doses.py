"""Expected doses, dose marks and adherence (spec §9, F7; A15; A07/C08 open → AdherenceRules).

Pure logic, no database. The schedule comes from confirmed prescription versions: each version is
in force from its confirmation until the next one, so a dose belongs to the version that was current
at its scheduled time. A drug keeps its ``course_id`` across versions, so marks survive re-confirmation.
"""

from collections import Counter
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import StrEnum
from zoneinfo import ZoneInfo

from kompas.modules.knowledge.care_rules import AdherenceRules, ReminderDefaults
from kompas.modules.therapy.domain.basket import DailySchedule, DoseRegimen


@dataclass(frozen=True, slots=True)
class CourseRegimen:
    """One drug of a prescription version."""

    course_id: str
    name: str  # trade name shown to the patient
    regimen: DoseRegimen | None


@dataclass(frozen=True, slots=True)
class PrescriptionWindow:
    """A confirmed version and the period it was in force (``until`` None = still current)."""

    prescription_id: str
    since: datetime
    until: datetime | None
    courses: tuple[CourseRegimen, ...]


@dataclass(frozen=True, slots=True)
class ExpectedDose:
    """One scheduled intake. ``scheduled_at`` is aware UTC; ``day``/``at`` are the patient's local ones."""

    course_id: str
    prescription_id: str
    name: str
    scheduled_at: datetime
    day: date
    at: time
    amount_mg: Decimal


def expected_doses(windows: Iterable[PrescriptionWindow], tz: ZoneInfo, since: date, until: date) -> list[ExpectedDose]:
    """All scheduled (daily) doses with a local day in ``[since, until]``, each attributed to the version
    in force at its time. "As needed" drugs have no expected doses."""
    doses: list[ExpectedDose] = []
    for window in windows:
        for course in window.courses:
            regimen = course.regimen
            if regimen is None or not isinstance(regimen.schedule, DailySchedule):
                continue
            first, last = max(regimen.start, since), min(regimen.end, until)
            day = first
            while day <= last:
                for intake in regimen.schedule.intakes:
                    moment = datetime.combine(day, intake.at, tz).astimezone(UTC)
                    if moment >= window.since and (window.until is None or moment < window.until):
                        doses.append(
                            ExpectedDose(
                                course.course_id,
                                window.prescription_id,
                                course.name,
                                moment,
                                day,
                                intake.at,
                                intake.amount_mg,
                            )
                        )
                day += timedelta(days=1)
    doses.sort(key=lambda d: (d.scheduled_at, d.name))
    return doses


class MarkStatus(StrEnum):
    TAKEN = "taken"
    SKIPPED = "skipped"


@dataclass(frozen=True, slots=True)
class Mark:
    """The patient's mark on one dose."""

    status: MarkStatus
    taken_at: datetime | None
    reason: str | None


class DoseState(StrEnum):
    """What the calendar shows: «вовремя / с опозданием / пропуск / нет отметки» + not yet due."""

    ON_TIME = "on_time"
    LATE = "late"
    SKIPPED = "skipped"
    UNMARKED = "unmarked"  # past the late limit without any mark
    DUE = "due"  # can be marked now, no mark yet
    UPCOMING = "upcoming"


def dose_state(dose: ExpectedDose, mark: Mark | None, now: datetime, rules: AdherenceRules) -> DoseState:
    """Classify one dose at ``now``."""
    late_limit = dose.scheduled_at + timedelta(minutes=rules.late_after_minutes)
    if mark is not None:
        if mark.status is MarkStatus.SKIPPED:
            return DoseState.SKIPPED
        taken = mark.taken_at or dose.scheduled_at
        return DoseState.LATE if taken > late_limit else DoseState.ON_TIME
    if now > late_limit:
        return DoseState.UNMARKED
    if now >= dose.scheduled_at - timedelta(minutes=rules.mark_before_minutes):
        return DoseState.DUE
    return DoseState.UPCOMING


def mark_problem(dose: ExpectedDose, now: datetime, rules: AdherenceRules) -> str | None:
    """Why the patient can't mark this dose now (None = allowed): too early, or older than the
    backfill window."""
    if now < dose.scheduled_at - timedelta(minutes=rules.mark_before_minutes):
        return f"Отметить приём можно не раньше чем за {rules.mark_before_minutes} мин до времени приёма"
    if now > dose.scheduled_at + timedelta(hours=rules.backfill_hours):
        return f"Отметку можно внести в течение {rules.backfill_hours} ч после времени приёма"
    return None


class Band(StrEnum):
    GREEN = "green"
    YELLOW = "yellow"
    RED = "red"


@dataclass(frozen=True, slots=True)
class AdherenceSummary:
    """Counts over closed doses (not DUE/UPCOMING) of a period. ``percent`` is None when nothing is
    countable; ``unmarked`` is always reported separately (A12), whatever the formula."""

    expected: int
    on_time: int
    late: int
    skipped: int
    unmarked: int
    percent: float | None
    band: Band | None
    skip_reasons: Mapping[str, int]


def summarize(states: Sequence[tuple[DoseState, Mark | None]], rules: AdherenceRules) -> AdherenceSummary:
    """Percent = taken / countable, where countable = taken + skipped (+ unmarked if
    ``rules.unmarked_counts_as_missed``)."""
    counts = Counter(state for state, _ in states)
    taken = counts[DoseState.ON_TIME] + counts[DoseState.LATE]
    countable = (
        taken + counts[DoseState.SKIPPED] + (counts[DoseState.UNMARKED] if rules.unmarked_counts_as_missed else 0)
    )
    percent = round(100 * taken / countable, 1) if countable else None
    band = None
    if percent is not None:
        band = Band.GREEN if percent >= rules.green_from else Band.YELLOW if percent >= rules.yellow_from else Band.RED
    reasons = Counter(m.reason or "other" for state, m in states if state is DoseState.SKIPPED and m is not None)
    closed = sum(v for k, v in counts.items() if k not in (DoseState.DUE, DoseState.UPCOMING))
    return AdherenceSummary(
        expected=closed,
        on_time=counts[DoseState.ON_TIME],
        late=counts[DoseState.LATE],
        skipped=counts[DoseState.SKIPPED],
        unmarked=counts[DoseState.UNMARKED],
        percent=percent,
        band=band,
        skip_reasons=dict(reasons),
    )


@dataclass(frozen=True, slots=True)
class SkipStreak:
    """``days`` consecutive local days ending on ``last_day`` where each day had a skipped or unmarked
    dose of the course."""

    course_id: str
    name: str
    last_day: date
    days: int


def skip_streaks(
    doses: Sequence[ExpectedDose], states: Sequence[DoseState], rules: AdherenceRules, now: datetime
) -> list[SkipStreak]:
    """Running streaks of at least ``rules.skip_streak_days`` local days in which each day had a
    skipped dose of the course — or an unmarked one, if ``rules.unmarked_counts_as_missed`` and the
    marking window (``backfill_hours``) has closed, so a patient who marks late is never flagged early.
    A streak ends on the latest settled day of the course; ``days`` counts back to its true start
    within the given doses (callers pass a window long enough to see it)."""
    bad: dict[str, set[date]] = {}
    settled_days: dict[str, set[date]] = {}
    names: dict[str, str] = {}
    backfill = timedelta(hours=rules.backfill_hours)
    for dose, state in zip(doses, states, strict=True):
        names[dose.course_id] = dose.name
        if state in (DoseState.DUE, DoseState.UPCOMING):
            continue
        if state is DoseState.UNMARKED and now <= dose.scheduled_at + backfill:
            continue  # can still be marked: not settled yet
        settled_days.setdefault(dose.course_id, set()).add(dose.day)
        if state is DoseState.SKIPPED or (state is DoseState.UNMARKED and rules.unmarked_counts_as_missed):
            bad.setdefault(dose.course_id, set()).add(dose.day)
    streaks = []
    for course_id, days in settled_days.items():
        last = max(days)
        run, day = 0, last
        while day in bad.get(course_id, set()):
            run += 1
            day -= timedelta(days=1)
        if run >= rules.skip_streak_days:
            streaks.append(SkipStreak(course_id, names[course_id], last, run))
    return streaks


def reminder_times(dose: ExpectedDose, rule: ReminderDefaults) -> list[tuple[int, datetime]]:
    """(attempt number, send time) for one dose: the first at ``scheduled + offset``, then repeats."""
    if not rule.enabled:
        return []
    first = dose.scheduled_at + timedelta(minutes=rule.first_offset_minutes)
    return [(i, first + timedelta(minutes=i * rule.repeat_every_minutes)) for i in range(rule.max_repeats + 1)]
