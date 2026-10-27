"""Expected doses, states, adherence summary, skip streaks and reminder times."""

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from zoneinfo import ZoneInfo

from kompas.modules.adherence.domain.doses import (
    Band,
    CourseRegimen,
    DoseState,
    Mark,
    MarkStatus,
    PrescriptionWindow,
    dose_state,
    expected_doses,
    mark_problem,
    reminder_times,
    skip_streaks,
    summarize,
)
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.therapy.domain.basket import DailySchedule, DoseRegimen, Intake, PrnSchedule

MSK = ZoneInfo("Europe/Moscow")


def regimen(start: date = date(2026, 10, 20), days: int = 10, *times: str) -> DoseRegimen:
    intakes = tuple(Intake(time.fromisoformat(t), Decimal(50)) for t in (times or ("08:00",)))
    return DoseRegimen(DailySchedule(intakes), start, days)


def window(
    *courses: CourseRegimen, since: datetime, until: datetime | None = None, pid: str = "rx1"
) -> PrescriptionWindow:
    return PrescriptionWindow(pid, since, until, courses)


def test_doses_follow_local_time_and_version_windows() -> None:
    # v1 confirmed 10:00 Moscow on the 20th (that day's 08:00 dose predates it); v2 from 09:00 on the 22nd
    v1 = window(
        CourseRegimen("c1", "Сертралин", regimen(date(2026, 10, 20), 10, "08:00", "20:00")),
        since=datetime(2026, 10, 20, 7, 0, tzinfo=UTC),
        until=datetime(2026, 10, 22, 6, 0, tzinfo=UTC),
        pid="v1",
    )
    v2 = window(
        CourseRegimen("c1", "Сертралин", regimen(date(2026, 10, 20), 10, "09:00")),
        since=datetime(2026, 10, 22, 6, 0, tzinfo=UTC),
        pid="v2",
    )
    doses = expected_doses([v1, v2], MSK, date(2026, 10, 20), date(2026, 10, 22))
    assert [(d.day.isoformat(), d.at.isoformat("minutes"), d.prescription_id) for d in doses] == [
        ("2026-10-20", "20:00", "v1"),
        ("2026-10-21", "08:00", "v1"),
        ("2026-10-21", "20:00", "v1"),
        ("2026-10-22", "08:00", "v1"),
        ("2026-10-22", "09:00", "v2"),
    ]
    assert doses[0].scheduled_at == datetime(2026, 10, 20, 17, 0, tzinfo=UTC)


def test_prn_has_no_expected_doses() -> None:
    prn = DoseRegimen(PrnSchedule(Decimal("0.5"), 3), date(2026, 10, 20), 10)
    w = window(CourseRegimen("c2", "Алпразолам", prn), since=datetime(2026, 10, 1, tzinfo=UTC))
    assert expected_doses([w], MSK, date(2026, 10, 20), date(2026, 10, 25)) == []


def test_states_and_mark_window(knowledge: Knowledge) -> None:
    rules = knowledge.adherence_rules
    w = window(CourseRegimen("c1", "Сертралин", regimen()), since=datetime(2026, 10, 1, tzinfo=UTC))
    dose = expected_doses([w], MSK, date(2026, 10, 20), date(2026, 10, 20))[0]  # 05:00 UTC
    at = dose.scheduled_at
    assert dose_state(dose, None, at - timedelta(hours=3), rules) is DoseState.UPCOMING
    assert dose_state(dose, None, at - timedelta(hours=1), rules) is DoseState.DUE
    assert dose_state(dose, None, at + timedelta(hours=3), rules) is DoseState.UNMARKED
    taken = Mark(MarkStatus.TAKEN, at + timedelta(minutes=30), None)
    late = Mark(MarkStatus.TAKEN, at + timedelta(hours=3), None)
    assert dose_state(dose, taken, at, rules) is DoseState.ON_TIME
    assert dose_state(dose, late, at, rules) is DoseState.LATE
    assert dose_state(dose, Mark(MarkStatus.SKIPPED, None, "forgot"), at, rules) is DoseState.SKIPPED
    assert mark_problem(dose, at - timedelta(hours=3), rules)
    assert mark_problem(dose, at + timedelta(hours=49), rules)
    assert mark_problem(dose, at + timedelta(hours=47), rules) is None


def test_summary_counts_unmarked_per_policy(knowledge: Knowledge) -> None:
    rules = knowledge.adherence_rules
    taken = Mark(MarkStatus.TAKEN, None, None)
    skipped = Mark(MarkStatus.SKIPPED, None, "intolerance")
    states = [(DoseState.ON_TIME, taken)] * 7 + [
        (DoseState.SKIPPED, skipped),
        (DoseState.UNMARKED, None),
        (DoseState.UPCOMING, None),
    ]
    summary = summarize(states, rules)
    assert (summary.expected, summary.unmarked, summary.percent, summary.band) == (9, 1, 77.8, Band.YELLOW)
    assert summary.skip_reasons == {"intolerance": 1}
    lenient = rules.model_copy(update={"unmarked_counts_as_missed": False})
    assert summarize(states, lenient).percent == 87.5
    assert summarize(states, lenient).band is Band.GREEN
    assert summarize([], rules).percent is None


def test_skip_streak_needs_consecutive_settled_days(knowledge: Knowledge) -> None:
    rules = knowledge.adherence_rules  # 3 days, backfill 48 h
    w = window(CourseRegimen("c1", "Сертралин", regimen()), since=datetime(2026, 10, 1, tzinfo=UTC))
    doses = expected_doses([w], MSK, date(2026, 10, 20), date(2026, 10, 24))
    states = [DoseState.ON_TIME, DoseState.SKIPPED, DoseState.UNMARKED, DoseState.SKIPPED, DoseState.UPCOMING]
    later = datetime(2026, 11, 1, tzinfo=UTC)
    [streak] = skip_streaks(doses, states, rules, later)
    assert (streak.last_day, streak.days) == (date(2026, 10, 23), 3)
    # the unmarked dose of the 22nd can still be marked 30 h later: not settled, so no streak yet
    assert skip_streaks(doses, states, rules, doses[2].scheduled_at + timedelta(hours=30)) == []
    lenient = rules.model_copy(update={"unmarked_counts_as_missed": False})  # A07 policy switch
    assert skip_streaks(doses, states, lenient, later) == []
    states[2] = DoseState.ON_TIME
    assert skip_streaks(doses, states, rules, later) == []


def test_long_streak_keeps_its_first_day(knowledge: Knowledge) -> None:
    rules = knowledge.adherence_rules
    w = window(CourseRegimen("c1", "Сертралин", regimen(date(2026, 10, 1), 40)), since=datetime(2026, 9, 1, tzinfo=UTC))
    doses = expected_doses([w], MSK, date(2026, 10, 1), date(2026, 10, 20))
    [streak] = skip_streaks(doses, [DoseState.SKIPPED] * len(doses), rules, datetime(2026, 11, 1, tzinfo=UTC))
    assert streak.last_day - timedelta(days=streak.days - 1) == date(2026, 10, 1)


def test_reminder_times(knowledge: Knowledge) -> None:
    rule = knowledge.adherence_rules.reminders  # at time, then every 30 min, 2 repeats
    w = window(CourseRegimen("c1", "Сертралин", regimen()), since=datetime(2026, 10, 1, tzinfo=UTC))
    dose = expected_doses([w], MSK, date(2026, 10, 20), date(2026, 10, 20))[0]
    times = reminder_times(dose, rule)
    assert [(i, t - dose.scheduled_at) for i, t in times] == [
        (0, timedelta(0)),
        (1, timedelta(minutes=30)),
        (2, timedelta(minutes=60)),
    ]
    assert reminder_times(dose, rule.model_copy(update={"enabled": False})) == []
