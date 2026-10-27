"""Diary triage: «Стабильно» / «Требует внимания» / «Срочно» (D-032, D-033, D-035).

Three layers, combined by taking the most severe level:
1. rules on the structured answers of one entry (instant, deterministic);
2. a text model on the free text (``text_model``) — can only raise the level;
3. trends across recent entries (e.g. mood dropping over several days).
Diary v2 entries (mood 0–2, anxiety/irritability 0–3, sleep in hours) get their own entry rules and
streaks (``evaluate_entry_v2``, ``evaluate_streaks``); old 0–10 entries keep ``evaluate_trend`` and
the two versions are never mixed.
Critical findings (safety question «да», a critical complaint) are reported separately as
``critical_hits`` so the alert pipeline never has to infer them from the level (D-030).
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date, timedelta
from enum import StrEnum
from itertools import combinations

from kompas.modules.diary.domain.entry import DiaryEntry
from kompas.modules.knowledge.complaints import ComplaintCatalog
from kompas.modules.knowledge.triage_rules import DiaryCondition, TriageLevel, TriageRules
from kompas.modules.monitoring.domain.text_model import ModelVerdict, TriageOption


@dataclass(frozen=True, slots=True)
class PatientContext:
    """Facts about the patient that change how an entry is read."""

    on_antidepressant: bool


class ReasonSource(StrEnum):
    RULE = "rule"
    MODEL = "model"
    TREND = "trend"


@dataclass(frozen=True, slots=True)
class TriageReason:
    """Why an entry got a level; shown to the doctor next to the entry."""

    level: TriageLevel
    source: ReasonSource
    code: str
    message: str


@dataclass(frozen=True, slots=True)
class DiaryCriticalHit:
    """A finding that must create an alert immediately (safety question, critical complaint)."""

    code: str  # "self_harm_thoughts" or a complaint code
    message: str


@dataclass(frozen=True, slots=True)
class TriageResult:
    """Final level plus every reason.

    ``model_unsure``: the model said «Неоднозначно» or was below the confidence threshold.
    ``model_unavailable``: the model failed; keyword verdict used instead.
    """

    level: TriageLevel
    reasons: tuple[TriageReason, ...]
    critical_hits: tuple[DiaryCriticalHit, ...]
    model_unsure: bool = False
    model_unavailable: bool = False


def evaluate_entry(
    entry: DiaryEntry,
    rules: TriageRules,
    complaints: ComplaintCatalog,
    context: PatientContext,
) -> tuple[list[TriageReason], list[DiaryCriticalHit]]:
    """Layer 1: safety question (yes → URGENT + critical hit; unanswered →
    ``rules.safety_question_unanswered``), complaint rules, critical complaints, side effects."""
    reasons: list[TriageReason] = []
    hits: list[DiaryCriticalHit] = []
    if entry.self_harm_thoughts is True:
        reasons.append(
            TriageReason(
                TriageLevel.URGENT, ReasonSource.RULE, "self_harm_thoughts", "Ответ «да» на вопрос о самоповреждении"
            )
        )
        hits.append(DiaryCriticalHit("self_harm_thoughts", "Пациент сообщил о мыслях о самоповреждении"))
    elif entry.self_harm_thoughts is None:
        reasons.append(
            TriageReason(
                rules.safety_question_unanswered,
                ReasonSource.RULE,
                "safety_question_unanswered",
                "Вопрос о самоповреждении пропущен",
            )
        )
    for rule in rules.complaint_rules:
        if rule.only_on_antidepressant and not context.on_antidepressant:
            continue
        matched = sorted(set(rule.complaints) & entry.complaints)
        if matched:
            reasons.append(TriageReason(rule.level, ReasonSource.RULE, "complaint:" + ",".join(matched), rule.message))
    for code in sorted(entry.complaints & complaints.critical_codes()):
        hits.append(DiaryCriticalHit(code, f"Отмечена жалоба: {complaints.get(code).name}"))
    if entry.side_effects:
        reasons.append(
            TriageReason(
                rules.side_effect_level,
                ReasonSource.RULE,
                "side_effects",
                "Побочные эффекты: " + ", ".join(entry.side_effects),
            )
        )
    return reasons, hits


def evaluate_trend(history: Sequence[DiaryEntry], rules: TriageRules) -> list[TriageReason]:
    """Layer 3: patterns across entries (order-independent; skipped scores ignored), e.g. a mood
    drop of ``rules.mood_drop_points`` within ``rules.mood_drop_days`` days."""
    scored = sorted((e for e in history if e.mood is not None), key=lambda e: e.day)
    worst: tuple[int, DiaryEntry, DiaryEntry] | None = None
    for earlier, later in combinations(scored, 2):
        assert earlier.mood is not None and later.mood is not None
        span = (later.day - earlier.day).days
        drop = earlier.mood - later.mood
        if 0 < span <= rules.mood_drop_days and drop >= rules.mood_drop_points and (worst is None or drop > worst[0]):
            worst = (drop, earlier, later)
    if worst is None:
        return []
    drop, earlier, later = worst
    return [
        TriageReason(
            TriageLevel.ATTENTION,
            ReasonSource.TREND,
            "mood_drop",
            f"Настроение снизилось на {drop} балла: {earlier.mood} ({earlier.day:%d.%m}) → "
            f"{later.mood} ({later.day:%d.%m})",
        )
    ]


_MOODS = ("депрессивное", "нормальное", "повышенное")


def matches(condition: DiaryCondition, entry: DiaryEntry) -> bool:
    """Every field given in ``condition`` holds for ``entry``; an unanswered field never matches."""
    checks: list[bool] = []
    if condition.mood is not None:
        checks.append(entry.mood in condition.mood)
    if condition.anxiety_min is not None:
        checks.append(entry.anxiety is not None and entry.anxiety >= condition.anxiety_min)
    if condition.irritability_min is not None:
        checks.append(entry.irritability is not None and entry.irritability >= condition.irritability_min)
    if condition.sleep_hours_below is not None:
        checks.append(entry.sleep_hours is not None and entry.sleep_hours < condition.sleep_hours_below)
    if condition.sleep_hours_above is not None:
        checks.append(entry.sleep_hours is not None and entry.sleep_hours > condition.sleep_hours_above)
    if condition.unusual_experiences is not None:
        checks.append(entry.unusual_experiences is condition.unusual_experiences)
    return bool(checks) and all(checks)


def _sleep(hours: float) -> str:
    minutes = round(hours * 60)
    return f"{minutes // 60}:{minutes % 60:02d}"


def observed(condition: DiaryCondition, entry: DiaryEntry) -> str:
    """The values ``condition`` looked at, e.g. «настроение повышенное, сон 3:30»."""
    parts: list[str] = []
    if condition.mood is not None and entry.mood is not None:
        parts.append(f"настроение {_MOODS[entry.mood]}")
    if condition.anxiety_min is not None:
        parts.append(f"тревога {entry.anxiety}/3")
    if condition.irritability_min is not None:
        parts.append(f"раздражительность {entry.irritability}/3")
    if (
        condition.sleep_hours_below is not None or condition.sleep_hours_above is not None
    ) and entry.sleep_hours is not None:
        parts.append(f"сон {_sleep(entry.sleep_hours)}")
    if condition.unusual_experiences is not None:
        parts.append("необычные переживания: да" if entry.unusual_experiences else "необычные переживания: нет")
    return ", ".join(parts)


def evaluate_entry_v2(entry: DiaryEntry, rules: TriageRules) -> list[TriageReason]:
    """Single-entry rules of diary v2 (``rules.diary_v2.entry_rules``)."""
    return [
        TriageReason(rule.level, ReasonSource.RULE, rule.code, f"{rule.message}: {observed(rule.when, entry)}")
        for rule in rules.diary_v2.entry_rules
        if matches(rule.when, entry)
    ]


def streak_window(rules: TriageRules) -> int:
    """How many days before and after an entry the streak rules can look at."""
    return max((rule.days for rule in rules.diary_v2.streak_rules), default=1) - 1


def evaluate_streaks(entry: DiaryEntry, history: Sequence[DiaryEntry], rules: TriageRules) -> list[TriageReason]:
    """Streak rules of diary v2: the run of consecutive calendar days matching ``when`` that contains
    ``entry.day`` is at least ``days`` long. ``history`` may hold any entries; only other v2 days count,
    and a day without an entry breaks the run."""
    by_day: dict[date, DiaryEntry] = {e.day: e for e in history if e.schema_version == 2 and e.day != entry.day}
    by_day[entry.day] = entry
    reasons: list[TriageReason] = []
    for rule in rules.diary_v2.streak_rules:
        if not matches(rule.when, entry):
            continue
        start = end = entry.day
        while (prev := by_day.get(start - timedelta(days=1))) is not None and matches(rule.when, prev):
            start = prev.day
        while (nxt := by_day.get(end + timedelta(days=1))) is not None and matches(rule.when, nxt):
            end = nxt.day
        length = (end - start).days + 1
        if length < rule.days:
            continue
        days = [by_day[start + timedelta(days=i)] for i in range(length)]
        values = "; ".join(f"{observed(rule.when, e)} ({e.day:%d.%m})" for e in days)
        reasons.append(
            TriageReason(rule.level, ReasonSource.TREND, rule.code, f"{rule.message} ({length} дн.): {values}")
        )
    return reasons


def model_reason(verdict: ModelVerdict, rules: TriageRules) -> tuple[TriageReason, bool]:
    """Layer 2: turn a model verdict into a reason. «Неоднозначно» or top probability below
    ``rules.model_unsure_threshold`` → ATTENTION; returns (reason, unsure)."""
    option, probability = verdict.top()
    if option is TriageOption.UNSURE or probability < rules.model_unsure_threshold:
        reason = TriageReason(
            TriageLevel.ATTENTION,
            ReasonSource.MODEL,
            "model_unsure",
            f"Модель не уверена в оценке текста ({verdict.model_name}, {probability:.0%}) — прочитайте запись",
        )
        return reason, True
    level = TriageLevel(option.value)
    message = {
        TriageLevel.STABLE: "Текст без признаков ухудшения",
        TriageLevel.ATTENTION: "В тексте есть признаки ухудшения",
        TriageLevel.URGENT: "В тексте есть признаки опасности",
    }[level]
    return TriageReason(level, ReasonSource.MODEL, f"model_{level.value}", f"{message} ({verdict.model_name})"), False


def combine(
    reasons: Sequence[TriageReason],
    critical_hits: Sequence[DiaryCriticalHit],
    *,
    model_unsure: bool = False,
    model_unavailable: bool = False,
) -> TriageResult:
    """Final level = most severe reason (STABLE when there are none)."""
    level = max((r.level for r in reasons), key=lambda lv: lv.rank, default=TriageLevel.STABLE)
    if critical_hits:
        level = TriageLevel.URGENT
    ordered = tuple(sorted(reasons, key=lambda r: -r.level.rank))
    return TriageResult(level, ordered, tuple(critical_hits), model_unsure, model_unavailable)
