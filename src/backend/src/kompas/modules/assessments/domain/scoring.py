"""The scoring engine: turns raw answers into a result according to a ``ScaleDefinition``.

Pure function of (definition, answers). The result snapshots the bands it used (so it re-renders
even if the definition changes later) and carries a step-by-step explanation for the doctor and
the CLI.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal

from kompas.core.errors import InvalidInputError, Problem
from kompas.modules.knowledge.scales import (
    AggregateSubscale,
    Band,
    BandCritical,
    ConditionalBandsScoring,
    CountCritical,
    CountScoring,
    ItemCritical,
    MeanScoring,
    NoScoring,
    Outcome,
    ProfileScoring,
    RuleScoring,
    ScaleDefinition,
    SingleScoring,
    SubscaleScoring,
    SumScoring,
    TotalCritical,
    scoring_ids,
    visible_items,
)
from kompas.modules.knowledge.triage_rules import UrgentScenario

type Answers = Mapping[int | str, int]
"""Item id → selected option value."""


@dataclass(frozen=True, slots=True)
class SubscoreResult:
    """One subscale's value and band (``formula_subscales`` method)."""

    code: str
    name: str
    value: float
    band: Band | None
    norms: dict[str, int] | None = None


@dataclass(frozen=True, slots=True)
class CriticalHit:
    """A critical rule that fired (alerts are created from these, D-030).

    ``item``/``value`` are set for item rules; ``band_code`` for band rules.
    """

    note: str | None
    proposal: bool
    item: int | str | None = None
    value: int | None = None
    band_code: str | None = None
    scenario: UrgentScenario | None = None  # the rule's own urgent scenario; None = the default


@dataclass(frozen=True, slots=True)
class ScoreResult:
    """Outcome of scoring one response.

    ``total`` is None for methods without a total (subscales, rule). ``band`` is None for
    ``formula_subscales`` (each subscale has its own band and patient text).
    """

    scale_code: str
    scale_version: int
    total: float | None
    band: Outcome | None
    subscores: tuple[SubscoreResult, ...]
    critical_hits: tuple[CriticalHit, ...]
    explanation: tuple[str, ...]

    @property
    def is_critical(self) -> bool:
        """True when at least one critical rule fired."""
        return bool(self.critical_hits)


def validate_answers(definition: ScaleDefinition, answers: Answers) -> None:
    problems: list[Problem] = []
    known = {item.id: item for item in definition.items}
    visible = {item.id for item in visible_items(definition, answers)}
    for item_id, item in known.items():
        if item_id not in answers:
            if item_id in visible:
                problems.append(Problem("answer_missing", f"Нет ответа на пункт {item_id}", f"answers.{item_id}"))
        elif not item.accepts(answers[item_id]):
            problems.append(Problem("answer_invalid", f"Недопустимый ответ на пункт {item_id}", f"answers.{item_id}"))
    for item_id in answers:
        if item_id not in known:
            problems.append(Problem("answer_unknown", f"В шкале нет пункта {item_id}", f"answers.{item_id}"))
    if problems:
        raise InvalidInputError(f"Ответы на шкалу {definition.code} некорректны", tuple(problems))


def _find_band(bands: Sequence[Band], value: float, half_open: bool = False) -> Band:
    ordered = sorted(bands, key=lambda b: b.lower)
    for i, band in enumerate(ordered):
        last = i == len(ordered) - 1
        if half_open and not last:
            if band.lower <= value < band.upper:
                return band
        elif band.lower <= value <= band.upper:
            return band
    # Validation guarantees full coverage of the reachable range; reaching here is a bug.
    raise AssertionError(f"no band for value {value}")


def _fmt(value: float) -> str:
    return f"{value:g}"


def item_critical_hits(definition: ScaleDefinition, answers: Answers) -> tuple[CriticalHit, ...]:
    """Critical valid answers survive partial/invalid submissions, including hidden stale answers."""
    valid = {i.id: answers[i.id] for i in definition.items if i.id in answers and i.accepts(answers[i.id])}
    hits: list[CriticalHit] = []
    for rule in definition.critical:
        if isinstance(rule, ItemCritical):
            value = valid.get(rule.item)
            if value is not None and value >= rule.min_value:
                hits.append(CriticalHit(rule.note, rule.proposal, item=rule.item, value=value, scenario=rule.scenario))
        elif isinstance(rule, CountCritical):
            count = sum(valid.get(i, rule.min_value - 1) >= rule.min_value for i in rule.items)
            if count >= rule.count_min:
                hits.append(CriticalHit(rule.note, rule.proposal, value=count, scenario=rule.scenario))
    return tuple(hits)


def _rounded(value: float, digits: int | None) -> float:
    return (
        value
        if digits is None
        else float(Decimal(str(value)).quantize(Decimal(1).scaleb(-digits), rounding=ROUND_HALF_UP))
    )


def _subscore(sub: AggregateSubscale, answers: Answers) -> SubscoreResult:
    values = [sub.reverse_base - answers[i] if i in sub.reverse else answers[i] for i in sub.items]
    if sub.weights:
        values = [sub.weights[i][answers[i]] for i in sub.items]
    value = sum(values) / (len(values) if sub.aggregate == "mean" else 1)
    norms = None
    if sub.norms:
        norms = {}
        for group in ("adults_and_young_men", "young_women"):
            for n in getattr(sub.norms, group):
                if n.lower <= value and (n.upper is None or value <= n.upper):
                    norms[group] = n.stanine
                    break
    return SubscoreResult(sub.code, sub.name, value, None, norms)


def score(definition: ScaleDefinition, answers: Answers) -> ScoreResult:
    validate_answers(definition, answers)
    active = {i.id for i in visible_items(definition, answers)}
    effective = {i: value for i, value in answers.items() if i in active}
    ids = [i for i in scoring_ids(definition) if i in active]
    steps: list[str] = list(definition.notes)
    total: float | None = None
    band: Outcome | None = None
    subscores: list[SubscoreResult] = []
    value: float
    scoring = definition.scoring
    match scoring:
        case SumScoring() | MeanScoring():
            values = [scoring.reverse_base - effective[i] if i in scoring.reverse else effective[i] for i in ids]
            total = sum(values) / (len(values) if isinstance(scoring, MeanScoring) else 1)
            interpreted = total
            if scoring.bands_basis == "percent_of_max":
                maximum = sum(max(o.value for o in definition.item(i).options) for i in ids)
                interpreted = total / maximum * 100
                steps.append(f"Доля от максимума: {interpreted:.2f}%")
            interpreted = _rounded(interpreted, scoring.round)
            if scoring.bands:
                band = _find_band(
                    scoring.bands, interpreted, half_open=isinstance(scoring, MeanScoring) and scoring.round is None
                )
            steps.append(
                f"{'Среднее' if isinstance(scoring, MeanScoring) else 'Сумма'} по {len(ids)} пунктам: {_fmt(total)}"
            )
            subscores.extend(_subscore(sub, effective) for sub in scoring.subscales)
            for name, bands in scoring.variants.items():
                variant = _find_band(bands, interpreted)
                steps.append(f"{name}: {variant.label}" + (" (предварительная граница)" if variant.provisional else ""))
        case SubscaleScoring():
            for sub in scoring.subscales:
                value = sum(effective[i] for i in sub.direct) - sum(effective[i] for i in sub.reverse) + sub.constant
                sub_band = _find_band(sub.bands, value)
                subscores.append(SubscoreResult(sub.code, sub.name, value, sub_band))
        case ProfileScoring():
            subscores.extend(_subscore(sub, effective) for sub in scoring.subscales)
            by_code = {sub.code: sub.value for sub in subscores}
            for index in scoring.indices:
                match index.aggregate:
                    case "mean_all":
                        value = sum(effective[i] for i in ids) / len(ids)
                    case "count_positive":
                        value = float(sum(effective[i] > 0 for i in ids))
                    case "mean_positive":
                        positives = [effective[i] for i in ids if effective[i] > 0]
                        value = sum(positives) / len(positives) if positives else 0.0
                    case "sum_subscores" | "mean_subscores":
                        value = sum(by_code[k] for k in index.subscales)
                        if index.aggregate == "mean_subscores":
                            value /= len(index.subscales)
                by_code[index.code] = value
                subscores.append(SubscoreResult(index.code, index.name, value, None))
            if scoring.bands_on is not None:
                total = by_code[scoring.bands_on]
                if scoring.bands:
                    band = _find_band(scoring.bands, _rounded(total, scoring.round), half_open=scoring.round is None)
        case CountScoring():
            total = float(sum(effective[i] == scoring.value for i in ids))
            if scoring.threshold is not None:
                band = scoring.positive if total >= scoring.threshold else scoring.negative
            steps.append(f"Ответов со значением {scoring.value}: {_fmt(total)}")
        case RuleScoring():
            band = scoring.default
            for case in scoring.cases:
                flags = []
                for cond in case.conditions:
                    refs = (cond.item,) if cond.item is not None else cond.items
                    count = sum(effective.get(i, cond.min_value - 1) >= cond.min_value for i in refs)
                    flags.append(count >= cond.count_min)
                if any(flags) if case.match == "any" else all(flags):
                    band = case.band
                    break
            steps.append("Интерпретация по сочетанию ответов")
        case ConditionalBandsScoring():
            total = float(sum(effective[i] for i in ids if i != scoring.selector_item))
            band = _find_band(scoring.bands_by_selector[effective[scoring.selector_item]], total)
        case SingleScoring():
            total = float(effective[scoring.item])
            band = _find_band(scoring.bands, total) if scoring.bands else None
        case NoScoring():
            steps.append("Ответы сохранены; суммарный балл и автоматическая интерпретация не предусмотрены")
            for item in definition.items:
                if item.id in effective:
                    label = next(
                        (o.label for o in item.options if o.value == effective[item.id]), str(effective[item.id])
                    )
                    steps.append(f"{item.id}: {label}")

    for result in subscores:
        steps.append(f"{result.name}: {_fmt(result.value)}" + (f" → {result.band.label}" if result.band else ""))
        if result.norms:
            steps.append(
                f"Станайны {result.name}: взрослые и юноши — {result.norms.get('adults_and_young_men')}; "
                f"девушки — {result.norms.get('young_women')}"
            )
    if band:
        steps.append(f"Интерпретация: {band.label}")
        if isinstance(band, Band) and band.provisional:
            steps.append("Граница диапазона предварительная: неоднозначность в исходном ключе")
    elif total is not None:
        steps.append("Пороговая интерпретация в источнике не задана")
    hits = list(item_critical_hits(definition, answers))
    for rule in definition.critical:
        if isinstance(rule, BandCritical) and band is not None and band.code in rule.band_codes:
            hits.append(CriticalHit(rule.note, rule.proposal, band_code=band.code, scenario=rule.scenario))
        elif isinstance(rule, TotalCritical) and total is not None and total >= rule.min_value:
            hits.append(CriticalHit(rule.note, rule.proposal, scenario=rule.scenario))
    steps.extend(f"Критический сигнал: {h.note or h.band_code or h.item}" for h in hits)
    return ScoreResult(definition.code, definition.version, total, band, tuple(subscores), tuple(hits), tuple(steps))
