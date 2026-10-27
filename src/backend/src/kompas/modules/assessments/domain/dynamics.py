"""Progress of one series over time: change from baseline, response and remission.

A series is one scale, or one subscale of it: key ``(scale_code, subscale_code | None)``. Values of
different series are never converted or averaged (C05). The criteria are configuration because
when "insufficient effect" becomes "resistance" is still open (A03): the engine only reports, the
doctor decides.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from kompas.modules.assessments.domain.scoring import ScoreResult
from kompas.modules.knowledge.scales import Direction

type SeriesKey = tuple[str, str | None]


@dataclass(frozen=True, slots=True)
class Measurement:
    """One value of one series, dated by when it was taken."""

    taken_on: date
    value: float


def measurements_of(result: ScoreResult, taken_on: date) -> tuple[tuple[SeriesKey, Measurement], ...]:
    """Split a result into series points: the total (if any) and each subscore."""
    points: list[tuple[SeriesKey, Measurement]] = []
    if result.total is not None:
        points.append(((result.scale_code, None), Measurement(taken_on, result.total)))
    for sub in result.subscores:
        points.append(((result.scale_code, sub.code), Measurement(taken_on, sub.value)))
    return tuple(points)


@dataclass(frozen=True, slots=True)
class ResponseCriteria:
    """Per-series, per-diagnosis criteria (from КР where available).

    ``response_reduction``: fraction of baseline, e.g. 0.5 for "improvement of at least 50%".
    ``remission_at_most``: value at or below which the patient is in remission (None = not defined).
    ``evaluate_after_days``: earliest day after baseline when a verdict makes sense.
    """

    response_reduction: float
    remission_at_most: float | None
    evaluate_after_days: int


class ResponseStatus(StrEnum):
    TOO_EARLY = "too_early"
    INSUFFICIENT_DATA = "insufficient_data"
    REMISSION = "remission"
    RESPONSE = "response"
    NO_RESPONSE = "no_response"


@dataclass(frozen=True, slots=True)
class ResponseAssessment:
    """Verdict plus the numbers behind it, for the doctor's screen (never shown to the patient).

    ``improvement``: fraction of baseline improved, sign-adjusted by direction (0.21 = 21% better).
    """

    status: ResponseStatus
    baseline: Measurement | None
    latest: Measurement | None
    improvement: float | None
    explanation: str


def assess_response(
    series: Sequence[Measurement],
    criteria: ResponseCriteria,
    direction: Direction,
    baseline_on: date,
) -> ResponseAssessment:
    """Compare the latest measurement with the baseline: the first measurement on or after
    ``baseline_on`` (normally the treatment start). Order of ``series`` does not matter.
    A zero baseline, or fewer than two usable points, gives INSUFFICIENT_DATA."""
    usable = sorted((m for m in series if m.taken_on >= baseline_on), key=lambda m: m.taken_on)
    if len(usable) < 2:
        return ResponseAssessment(
            ResponseStatus.INSUFFICIENT_DATA,
            usable[0] if usable else None,
            None,
            None,
            "Недостаточно измерений после начала терапии",
        )
    baseline, latest = usable[0], usable[-1]
    days = (latest.taken_on - baseline.taken_on).days
    if baseline.value == 0:
        return ResponseAssessment(
            ResponseStatus.INSUFFICIENT_DATA, baseline, latest, None, "Исходное значение равно нулю"
        )
    change = (latest.value - baseline.value) / baseline.value
    improvement = -change if direction is Direction.HIGHER_IS_WORSE else change
    numbers = f"{baseline.value:g} → {latest.value:g} ({improvement:+.0%} улучшение) за {days} дн."
    if days < criteria.evaluate_after_days:
        return ResponseAssessment(
            ResponseStatus.TOO_EARLY,
            baseline,
            latest,
            improvement,
            f"{numbers}; оценивать эффект рано (не раньше {criteria.evaluate_after_days} дн.)",
        )
    remission = criteria.remission_at_most is not None and (
        latest.value <= criteria.remission_at_most
        if direction is Direction.HIGHER_IS_WORSE
        else latest.value >= criteria.remission_at_most
    )
    if remission:
        return ResponseAssessment(ResponseStatus.REMISSION, baseline, latest, improvement, f"{numbers}; ремиссия")
    if improvement >= criteria.response_reduction:
        return ResponseAssessment(
            ResponseStatus.RESPONSE,
            baseline,
            latest,
            improvement,
            f"{numbers}; ответ на терапию (≥ {criteria.response_reduction:.0%})",
        )
    return ResponseAssessment(
        ResponseStatus.NO_RESPONSE,
        baseline,
        latest,
        improvement,
        f"{numbers}; ответа нет (порог {criteria.response_reduction:.0%}). Решение принимает врач.",
    )
