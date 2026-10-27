import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from kompas.cli import main
from kompas.core.errors import InvalidInputError, InvalidStateError
from kompas.json_input import parse_patient
from kompas.modules.diary.domain.entry import DiaryEntry
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.triage_rules import TriageLevel
from kompas.modules.monitoring.domain.alerts import (
    AlertEvent,
    AlertState,
    AlertStatus,
    patient_may_see_doctor_received,
    transition,
)
from kompas.modules.monitoring.domain.freshness import diary_freshness
from kompas.modules.monitoring.domain.text_model import KeywordModel, ModelVerdict, TriageOption
from kompas.modules.monitoring.domain.triage import (
    PatientContext,
    combine,
    evaluate_entry,
    evaluate_entry_v2,
    evaluate_streaks,
    evaluate_trend,
    model_reason,
)

from .conftest import BACKEND, OHLP_SAMPLE

DAY = date(2026, 10, 19)


def entry(**overrides: object) -> DiaryEntry:
    fields: dict[str, object] = {
        "day": DAY,
        "mood": 6,
        "anxiety": 4,
        "energy": 5,
        "sleep": None,
        "complaints": frozenset(),
        "side_effects": (),
        "self_harm_thoughts": False,
        "text": None,
    }
    fields.update(overrides)
    return DiaryEntry(**fields)  # type: ignore[arg-type]


def triage(knowledge: Knowledge, e: DiaryEntry, on_ad: bool = False) -> tuple[TriageLevel, list[str]]:
    reasons, hits = evaluate_entry(e, knowledge.triage_rules, knowledge.complaints, PatientContext(on_ad))
    result = combine(reasons, hits)
    return result.level, [h.code for h in result.critical_hits]


def test_quiet_entry_is_stable(knowledge: Knowledge) -> None:
    assert triage(knowledge, entry()) == (TriageLevel.STABLE, [])


def test_self_harm_yes_is_urgent_and_critical(knowledge: Knowledge) -> None:
    assert triage(knowledge, entry(self_harm_thoughts=True)) == (TriageLevel.URGENT, ["self_harm_thoughts"])


def test_unanswered_safety_question_is_not_read_as_no(knowledge: Knowledge) -> None:
    level, hits = triage(knowledge, entry(self_harm_thoughts=None))
    assert level is TriageLevel.ATTENTION and hits == []


def test_critical_complaint(knowledge: Knowledge) -> None:
    level, hits = triage(knowledge, entry(complaints=frozenset({"suicidal_thoughts"})))
    assert level is TriageLevel.URGENT and hits == ["suicidal_thoughts"]


def test_euphoria_only_matters_on_antidepressant(knowledge: Knowledge) -> None:
    e = entry(complaints=frozenset({"euphoria"}))
    assert triage(knowledge, e, on_ad=False)[0] is TriageLevel.STABLE
    assert triage(knowledge, e, on_ad=True)[0] is TriageLevel.ATTENTION


def test_mood_drop_trend(knowledge: Knowledge) -> None:
    history = [entry(day=DAY - timedelta(days=3), mood=7), entry(day=DAY, mood=3)]
    reasons = evaluate_trend(history, knowledge.triage_rules)
    assert [r.code for r in reasons] == ["mood_drop"]
    slow = [entry(day=DAY - timedelta(days=10), mood=7), entry(day=DAY, mood=3)]
    assert evaluate_trend(slow, knowledge.triage_rules) == []


def v2(day: date = DAY, **overrides: object) -> DiaryEntry:
    fields: dict[str, object] = {
        "day": day,
        "mood": 1,
        "anxiety": 0,
        "energy": None,
        "sleep": None,
        "complaints": frozenset(),
        "side_effects": (),
        "self_harm_thoughts": False,
        "text": None,
        "schema_version": 2,
        "irritability": 0,
        "sleep_hours": 8.0,
        "unusual_experiences": False,
    }
    fields.update(overrides)
    return DiaryEntry(**fields)  # type: ignore[arg-type]


def v2_codes(knowledge: Knowledge, e: DiaryEntry, history: list[DiaryEntry] | None = None) -> list[str]:
    rules = knowledge.triage_rules
    return [r.code for r in [*evaluate_entry_v2(e, rules), *evaluate_streaks(e, history or [], rules)]]


def test_v2_quiet_entry_has_no_reasons(knowledge: Knowledge) -> None:
    assert v2_codes(knowledge, v2()) == []


def test_v2_elevated_mood_is_not_better_mood(knowledge: Knowledge) -> None:
    """Mood 2 «повышенное» is a category: with short sleep or irritability it needs attention."""
    assert v2_codes(knowledge, v2(mood=2)) == []
    assert v2_codes(knowledge, v2(mood=2, sleep_hours=4.5)) == ["elevated_mood_short_sleep"]
    assert v2_codes(knowledge, v2(mood=2, irritability=2)) == ["elevated_mood_irritability"]
    assert v2_codes(knowledge, v2(mood=0, sleep_hours=4.5)) == []


def test_v2_unanswered_fields_never_match(knowledge: Knowledge) -> None:
    assert v2_codes(knowledge, v2(mood=None, sleep_hours=None, irritability=None, unusual_experiences=None)) == []
    assert v2_codes(knowledge, v2(unusual_experiences=True)) == ["unusual_experiences"]


def test_v2_streaks_need_consecutive_v2_days(knowledge: Knowledge) -> None:
    low = [v2(DAY - timedelta(days=2), mood=0), v2(DAY - timedelta(days=1), mood=0)]
    assert v2_codes(knowledge, v2(mood=0), low) == ["depressed_mood_streak"]
    assert v2_codes(knowledge, v2(mood=0), low[1:]) == []  # two days only
    gap = [v2(DAY - timedelta(days=3), mood=0), v2(DAY - timedelta(days=1), mood=0)]
    assert v2_codes(knowledge, v2(mood=0), gap) == []  # a day without an entry breaks the run
    old = [entry(day=DAY - timedelta(days=2), mood=0), v2(DAY - timedelta(days=1), mood=0)]
    assert v2_codes(knowledge, v2(mood=0), old) == []  # old 0–10 entries are never mixed in
    # a backfilled day in the middle completes the run on both sides
    around = [v2(DAY - timedelta(days=1), mood=0), v2(DAY + timedelta(days=1), mood=0)]
    assert v2_codes(knowledge, v2(mood=0), around) == ["depressed_mood_streak"]


def test_v2_streak_reason_shows_the_values(knowledge: Knowledge) -> None:
    rules = knowledge.triage_rules
    (reason,) = evaluate_streaks(v2(sleep_hours=3.0), [v2(DAY - timedelta(days=1), sleep_hours=3.5)], rules)
    assert reason.code == "short_sleep_streak" and reason.level is TriageLevel.ATTENTION
    assert reason.message.endswith("(2 дн.): сон 3:30 (18.10); сон 3:00 (19.10)")


def test_model_can_only_raise_and_unsure_maps_to_attention(knowledge: Knowledge) -> None:
    rules = knowledge.triage_rules

    def verdict(**probs: float) -> ModelVerdict:
        return ModelVerdict("m", "m@1", {TriageOption(k): v for k, v in probs.items()}, 1.0)

    urgent, unsure = model_reason(verdict(stable=0.05, attention=0.05, urgent=0.85, unsure=0.05), rules)
    assert urgent.level is TriageLevel.URGENT and not unsure
    low, unsure = model_reason(verdict(stable=0.4, attention=0.3, urgent=0.2, unsure=0.1), rules)
    assert low.level is TriageLevel.ATTENTION and unsure
    explicit, unsure = model_reason(verdict(stable=0.1, attention=0.1, urgent=0.1, unsure=0.7), rules)
    assert explicit.level is TriageLevel.ATTENTION and unsure
    # a stable model verdict never lowers a rule-based urgent level
    rule_reasons, hits = evaluate_entry(
        entry(self_harm_thoughts=True), rules, knowledge.complaints, PatientContext(False)
    )
    stable, _ = model_reason(verdict(stable=0.97, attention=0.01, urgent=0.01, unsure=0.01), rules)
    assert combine([*rule_reasons, stable], hits).level is TriageLevel.URGENT


def test_keyword_model(knowledge: Knowledge) -> None:
    model = KeywordModel(knowledge.triage_rules.keywords)
    assert model.classify("Не хочу больше жить").top()[0] is TriageOption.URGENT
    assert model.classify("плохо сплю").top()[0] is TriageOption.ATTENTION
    assert model.classify("гуляла с ребёнком").top()[0] is TriageOption.STABLE


def test_alert_lifecycle() -> None:
    t0 = datetime(2026, 10, 19, 21, 40, tzinfo=UTC)
    state = AlertState(AlertStatus.CREATED, t0)
    assert not patient_may_see_doctor_received(state)
    state = transition(state, AlertEvent.DISPATCH, t0)
    state = transition(state, AlertEvent.DISPATCH, t0 + timedelta(seconds=5))  # retry keeps first sent_at
    assert state.status is AlertStatus.SENT and state.sent_at == t0
    state = transition(state, AlertEvent.DELIVERY_CONFIRMED, t0 + timedelta(seconds=6))
    state = transition(state, AlertEvent.ACCEPT, t0 + timedelta(minutes=2))
    assert patient_may_see_doctor_received(state)
    with pytest.raises(InvalidInputError):
        transition(state, AlertEvent.HANDLE, t0 + timedelta(minutes=5), note="  ")
    state = transition(state, AlertEvent.HANDLE, t0 + timedelta(minutes=5), note="Позвонила, C-SSRS")
    assert state.status is AlertStatus.HANDLED
    with pytest.raises(InvalidStateError):
        transition(state, AlertEvent.ACCEPT, t0)


def test_accept_straight_from_created() -> None:
    t0 = datetime(2026, 10, 19, tzinfo=UTC)
    assert transition(AlertState(AlertStatus.CREATED, t0), AlertEvent.ACCEPT, t0).status is AlertStatus.ACCEPTED


def test_freshness() -> None:
    days = [DAY - timedelta(days=d) for d in (0, 1, 3)]
    f = diary_freshness(days, DAY, window_days=7, since=DAY - timedelta(days=30))
    assert (f.last_entry_on, f.days_since_last, f.missed_days, f.window_days) == (DAY, 0, 4, 7)
    assert not f.complete
    new_patient = diary_freshness([DAY], DAY, window_days=7, since=DAY)
    assert new_patient.complete and new_patient.window_days == 1


def test_patient_json_requires_explicit_lists() -> None:
    base = {
        "birth_date": "1992-03-01",
        "sex": "female",
        "intolerant_excipients": [],
        "past_intolerance": [],
        "past_inefficacy": [],
    }
    with pytest.raises(InvalidInputError) as exc:
        parse_patient(base)  # allergies missing: neither [] nor null
    assert exc.value.details[0].pointer == "patient.allergies"
    assert parse_patient({**base, "allergies": None}).allergies is None


# ------------------------------------------------------------------------------------------ CLI smoke tests


def run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, str]:
    code = main(["--ohlp", str(OHLP_SAMPLE), *args])
    return code, capsys.readouterr().out


def test_cli_scales(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "scales", "score", "PHQ-9", "--answers", "2,2,3,2,2,2,2,3,1")
    assert code == 0
    assert "Умеренно тяжёлая" in out and "КРИТИЧЕСКИЙ" in out
    assert "Ответы сохранены" in out and "Депрессивная симптоматика" not in out
    assert run(capsys, "scales", "validate")[0] == 0


def test_cli_basket_check_and_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    example = str(BACKEND / "examples" / "basket_anna.json")
    code, out = run(capsys, "basket", "check", example)
    assert code == 0 and "КРАСНАЯ рамка" in out
    code, out = run(capsys, "basket", "confirm", example)
    assert code == 1 and "НЕ подтверждено" in out
    code, out = run(capsys, "--json", "basket", "check", example)
    ids = [
        w["id"] for w in json.loads(out)["warnings"] if w["severity"] == "contraindicated" and w["involves_new_item"]
    ]
    args = [arg for wid in ids for arg in ("--justify", f"{wid}=Обоснование врача для теста")]
    code, out = run(capsys, "basket", "confirm", example, *args)
    assert code == 0 and "подтверждено" in out


def test_cli_triage(capsys: pytest.CaptureFixture[str]) -> None:
    code, out = run(capsys, "triage", "entry", str(BACKEND / "examples" / "diary_entry.json"))
    assert code == 0 and "ТРЕБУЕТ ВНИМАНИЯ" in out and "Настроение снизилось" in out


def test_cli_reports_domain_errors_without_traceback(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"patient": {"birth_date": "1992-03-01", "sex": "female"}}), encoding="utf-8")
    assert main(["--ohlp", str(OHLP_SAMPLE), "basket", "check", str(bad)]) == 1
    assert "allergies" in capsys.readouterr().err
