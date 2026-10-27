"""Scale definitions: the schema of ``knowledge_data/scales/*.yaml`` and their loader.

A scale definition is pure data. It describes items, answer options, how to score them, the score
bands with doctor and patient wording, critical rules, who fills it in and who may see the result.
The scoring engine (``assessments.domain.scoring``) interprets it; adding a scale never needs code.

Validation happens at load time, so a broken key (gaps, overlaps, unknown item ids, missing
patient texts) fails loudly when knowledge is loaded, never while a patient waits for a result.

Band bounds: for integer-valued methods (sum, count_matching, formula_subscales with integer
constants, conditional_bands) bands are inclusive integer intervals that must be contiguous.
Rule-based scales use plain outcomes without bounds.
For ``mean`` bands are half-open ``[lower, upper)``, except the last one, which is closed.
"""

import itertools
from collections.abc import Mapping, Sequence
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, BeforeValidator, ConfigDict, Discriminator, Field, Tag

from kompas.core.errors import DefinitionError, NotFoundError, Problem
from kompas.modules.knowledge._files import read_yaml, validate_model
from kompas.modules.knowledge.triage_rules import UrgentScenario


class FilledBy(StrEnum):
    """Who answers the items."""

    PATIENT = "patient"
    CLINICIAN = "clinician"
    PATIENT_CLINICIAN = "patient_clinician"


class Direction(StrEnum):
    """How to read a change over time."""

    HIGHER_IS_WORSE = "higher_is_worse"  # symptom scales: PHQ-9, HDRS-17, GAD-7, ...
    HIGHER_IS_BETTER = "higher_is_better"  # functioning scales (e.g. PSP)


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


def _item_id(value: Any) -> Any:
    return int(value) if isinstance(value, str) and value.isdecimal() else value


ItemId = Annotated[int | str, BeforeValidator(_item_id)]


class VisibilityCondition(_Model):
    item: ItemId
    equals: int


class Visibility(_Model):
    match: Literal["any", "all"] = "all"
    conditions: tuple[VisibilityCondition, ...] = Field(min_length=1)


class AnswerOption(_Model):
    """One selectable answer of an item and the points it is worth."""

    value: int
    label: str


class Item(_Model):
    """One question (patient scales) or rated symptom (clinician scales)."""

    id: ItemId
    text: str
    options: tuple[AnswerOption, ...] = ()
    input: Literal["number"] | None = None
    min: int = 0
    max: int | None = None
    scored: bool = True
    hint: str | None = None
    section: str | None = None
    show_if: Visibility | None = None
    pdf_number: int | str | None = None
    symptom: int | None = None
    situation: int | None = None
    screening: bool = False

    def accepts(self, value: int) -> bool:
        if isinstance(value, bool) or not isinstance(value, int):
            return False
        if self.input == "number":
            return value >= self.min and (self.max is None or value <= self.max)
        return value in {o.value for o in self.options}


def visible_items(definition: "ScaleDefinition", answers: Mapping[int | str, int]) -> tuple[Item, ...]:
    """Conditions can only refer to earlier items, so hidden stale answers never reveal children."""
    active: dict[int | str, int] = {}
    result = []
    for item in definition.items:
        if item.show_if:
            flags = [active.get(c.item) == c.equals for c in item.show_if.conditions]
            if not (any(flags) if item.show_if.match == "any" else all(flags)):
                continue
        result.append(item)
        if item.id in answers and item.accepts(answers[item.id]):
            active[item.id] = answers[item.id]
    return tuple(result)


class Outcome(_Model):
    """An interpretation a result can land in.

    ``label`` is the doctor's wording; the patient never sees outcomes (D-109). ``level`` is an
    ordinal severity 0..4 shared across scales, used for signal mapping. Rule-based scales use plain
    outcomes.
    """

    code: str
    label: str
    level: int = Field(ge=0, le=4)


class Band(Outcome):
    """An outcome tied to a score interval (interval semantics: see the module docstring)."""

    lower: float
    upper: float
    provisional: bool = False


class Stanine(_Model):
    stanine: int = Field(ge=1, le=9)
    lower: int
    upper: int | None


class Norms(_Model):
    kind: Literal["stanine"]
    adults_and_young_men: tuple[Stanine, ...]
    young_women: tuple[Stanine, ...]


class AggregateSubscale(_Model):
    code: str
    name: str
    items: tuple[ItemId, ...] = Field(min_length=1)
    aggregate: Literal["sum", "mean"] = "sum"
    reverse: tuple[ItemId, ...] = ()
    reverse_base: int = 0
    weights: dict[ItemId, dict[int, int]] = Field(default_factory=dict)
    norms: Norms | None = None
    norm_ru: str | None = None
    domain: str | None = None


class Index(_Model):
    code: str
    name: str
    aggregate: Literal["mean_all", "count_positive", "mean_positive", "sum_subscores", "mean_subscores"]
    subscales: tuple[str, ...] = ()
    norm_ru: str | None = None


class NumericScoring(_Model):
    bands: tuple[Band, ...] = ()
    subscales: tuple[AggregateSubscale, ...] = ()
    items: tuple[ItemId, ...] | None = None
    reverse: tuple[ItemId, ...] = ()
    reverse_base: int = 0
    round: int | None = Field(default=None, ge=0, le=6)
    bands_basis: Literal["raw", "percent_of_max"] = "raw"
    variants: dict[str, tuple[Band, ...]] = Field(default_factory=dict)


class SumScoring(NumericScoring):
    """Total = sum of item values; band looked up on the total."""

    method: Literal["sum"]


class MeanScoring(NumericScoring):
    """Total = mean of item values; band looked up on the mean (half-open bands)."""

    method: Literal["mean"]


class ProfileScoring(_Model):
    method: Literal["subscales"]
    subscales: tuple[AggregateSubscale, ...] = Field(min_length=1)
    indices: tuple[Index, ...] = ()
    bands_on: str | None = None
    bands: tuple[Band, ...] = ()
    round: int | None = Field(default=None, ge=0, le=6)


class SingleScoring(_Model):
    method: Literal["single_item"]
    item: ItemId
    bands: tuple[Band, ...] = ()


class NoScoring(_Model):
    method: Literal["none"]


class Subscale(_Model):
    """Subscore = sum(direct items) - sum(reverse items) + constant (e.g. STAI)."""

    code: str
    name: str
    direct: tuple[ItemId, ...]
    reverse: tuple[ItemId, ...] = ()
    constant: int = 0
    bands: tuple[Band, ...]


class SubscaleScoring(_Model):
    """Several independent subscores, each with its own bands and patient text; no overall total."""

    method: Literal["formula_subscales"]
    subscales: tuple[Subscale, ...]


class CountScoring(_Model):
    """Count items answered with ``value``; ``positive`` band when the count reaches ``threshold``.

    Validation: ``negative`` covers 0..threshold-1 and ``positive`` covers threshold..item count.
    """

    method: Literal["count_matching"]
    value: int
    items: tuple[ItemId, ...] | None = None
    threshold: int | None = None
    positive: Band | None = None
    negative: Band | None = None


class Condition(_Model):
    """``answer[item] >= min_value``."""

    item: ItemId | None = None
    items: tuple[ItemId, ...] = ()
    min_value: int
    count_min: int = Field(default=1, ge=1)


class RuleCase(_Model):
    """If any (or all) conditions hold, the result is ``band``. Cases are tried in order."""

    match: Literal["any", "all"] = "any"
    conditions: tuple[Condition, ...]
    band: Outcome


class RuleScoring(_Model):
    """Decision table without a total (e.g. C-SSRS). First matching case wins, else ``default``.
    Outcomes have no score bounds, so no coverage check applies."""

    method: Literal["rule"]
    cases: tuple[RuleCase, ...]
    default: Outcome


class ConditionalBandsScoring(_Model):
    """Sum of items except ``selector_item``; the band table depends on that item's answer (PDSS)."""

    method: Literal["conditional_bands"]
    selector_item: ItemId
    bands_by_selector: dict[int, tuple[Band, ...]]


Scoring = Annotated[
    SumScoring
    | MeanScoring
    | SubscaleScoring
    | CountScoring
    | RuleScoring
    | ConditionalBandsScoring
    | ProfileScoring
    | SingleScoring
    | NoScoring,
    Field(discriminator="method"),
]


class ItemCritical(_Model):
    """Critical when ``answer[item] >= min_value`` (e.g. PHQ-9 item 9 ≥ 1)."""

    kind: Literal["item"] = "item"
    item: ItemId
    min_value: int
    note: str | None = None
    proposal: bool = False  # True = team proposal awaiting clinical approval
    scenario: UrgentScenario | None = None  # None → triage.yaml urgent_scenarios.questionnaire (D-132)


class BandCritical(_Model):
    """Critical when the overall result falls in one of ``band_codes`` (e.g. C-SSRS serious/extreme)."""

    kind: Literal["band"] = "band"
    band_codes: tuple[str, ...]
    note: str | None = None
    proposal: bool = False
    scenario: UrgentScenario | None = None  # None → triage.yaml urgent_scenarios.questionnaire (D-132)


class CountCritical(_Model):
    kind: Literal["count"] = "count"
    items: tuple[ItemId, ...] = Field(min_length=1)
    min_value: int
    count_min: int = Field(ge=1)
    note: str | None = None
    proposal: bool = False
    scenario: UrgentScenario | None = None  # None → triage.yaml urgent_scenarios.questionnaire (D-132)


class TotalCritical(_Model):
    kind: Literal["total"] = "total"
    min_value: float
    note: str | None = None
    proposal: bool = False
    scenario: UrgentScenario | None = None  # None → triage.yaml urgent_scenarios.questionnaire (D-132)


def _critical_kind(value: Any) -> str:
    """Rules without an explicit ``kind`` are item rules (the common case in YAML)."""
    if isinstance(value, dict):
        return str(value.get("kind", "item"))
    return str(getattr(value, "kind", "item"))


CriticalRule = Annotated[
    Annotated[ItemCritical, Tag("item")]
    | Annotated[BandCritical, Tag("band")]
    | Annotated[CountCritical, Tag("count")]
    | Annotated[TotalCritical, Tag("total")],
    Discriminator(_critical_kind),
]
"""An answer or outcome that must raise an alert immediately, whatever else is true (D-030)."""


class ScaleDefinition(_Model):
    """A complete, validated scale definition (one YAML file).

    ``measures`` groups scales on charts (D-024), e.g. "depression", "anxiety", "suicide_risk".
    ``diagnoses`` lists ICD patterns the scale is recommended for (shown first when assigning).
    """

    code: str
    version: int
    name: str
    measures: str
    direction: Direction
    filled_by: FilledBy
    source: str
    licence_note: str | None = None
    instructions: str | None = None
    diagnoses: tuple[str, ...] = ()
    items: tuple[Item, ...]
    scoring: Scoring
    critical: tuple[CriticalRule, ...] = ()
    notes: tuple[str, ...] = ()
    references: dict[str, Any] = Field(default_factory=dict)

    def item(self, item_id: int | str) -> Item:
        """Return the item with ``item_id``.

        Raises:
            NotFoundError: no such item.
        """
        for item in self.items:
            if item.id == item_id:
                return item
        raise NotFoundError(f"В шкале {self.code} нет пункта {item_id}")

    def band_codes(self) -> frozenset[str]:
        """Every band code the scoring can produce (used to validate ``BandCritical``)."""
        return frozenset(band.code for band in all_bands(self.scoring))


def all_bands(scoring: Scoring) -> tuple[Outcome, ...]:
    """Every outcome (band or rule outcome) mentioned by a scoring block."""
    match scoring:
        case SumScoring() | MeanScoring() | ProfileScoring() | SingleScoring():
            return scoring.bands
        case SubscaleScoring():
            return tuple(b for s in scoring.subscales for b in s.bands)
        case CountScoring():
            return tuple(b for b in (scoring.negative, scoring.positive) if b is not None)
        case RuleScoring():
            return (*(c.band for c in scoring.cases), scoring.default)
        case ConditionalBandsScoring():
            return tuple(b for bands in scoring.bands_by_selector.values() for b in bands)
        case NoScoring():
            return ()


def _value_range(definition: ScaleDefinition, item_ids: Sequence[int | str]) -> tuple[int, int]:
    """Lowest and highest possible sum over ``item_ids`` (ids assumed to exist)."""
    by_id = {item.id: item for item in definition.items}
    lows = [by_id[i].min if by_id[i].input else min(o.value for o in by_id[i].options) for i in item_ids]
    highs = [by_id[i].max if by_id[i].input else max(o.value for o in by_id[i].options) for i in item_ids]
    if any(v is None for v in highs):
        raise ValueError("Числовое поле в расчёте должно иметь верхнюю границу")
    return sum(lows), sum(v for v in highs if v is not None)


def _check_integer_coverage(bands: Sequence[Band], low: float, high: float, where: str) -> list[str]:
    """Bands must be integer, ordered, contiguous and cover exactly ``low..high``."""
    problems: list[str] = []
    if not bands:
        return [f"{where}: нет ни одного диапазона"]
    if any(b.lower != int(b.lower) or b.upper != int(b.upper) for b in bands):
        problems.append(f"{where}: границы диапазонов должны быть целыми")
    ordered = sorted(bands, key=lambda b: b.lower)
    if ordered[0].lower != low:
        problems.append(f"{where}: первый диапазон начинается с {ordered[0].lower:g}, а минимум шкалы {low:g}")
    if ordered[-1].upper != high:
        problems.append(f"{where}: последний диапазон заканчивается на {ordered[-1].upper:g}, а максимум {high:g}")
    for band in ordered:
        if band.lower > band.upper:
            problems.append(f"{where}: диапазон {band.code} пустой ({band.lower:g}–{band.upper:g})")
    for prev, nxt in itertools.pairwise(ordered):
        if nxt.lower > prev.upper + 1:
            problems.append(f"{where}: пропуск между {prev.upper:g} и {nxt.lower:g}")
        elif nxt.lower <= prev.upper:
            problems.append(f"{where}: диапазоны {prev.code} и {nxt.code} пересекаются")
    return problems


def _check_halfopen_coverage(bands: Sequence[Band], low: float, high: float, where: str) -> list[str]:
    """Bands ``[lower, upper)`` (last one closed) must chain exactly from ``low`` to ``high``."""
    if not bands:
        return [f"{where}: нет ни одного диапазона"]
    ordered = sorted(bands, key=lambda b: b.lower)
    problems: list[str] = []
    if ordered[0].lower != low or ordered[-1].upper != high:
        problems.append(f"{where}: диапазоны должны покрывать {low:g}–{high:g}")
    for prev, nxt in itertools.pairwise(ordered):
        if nxt.lower != prev.upper:
            problems.append(f"{where}: диапазоны {prev.code} и {nxt.code} не стыкуются")
    return problems


def scoring_ids(definition: ScaleDefinition) -> tuple[int | str, ...]:
    selected = getattr(definition.scoring, "items", None)
    return tuple(selected) if selected is not None else tuple(i.id for i in definition.items if i.scored)


def validate_definition(definition: ScaleDefinition) -> list[str]:
    """Reject broken references, unsafe input ranges and gaps in declared interpretation bands."""
    problems: list[str] = []
    known = {item.id: item for item in definition.items}
    if not known or len(known) != len(definition.items):
        return ["шкала пуста или номера пунктов повторяются"]
    earlier: set[int | str] = set()
    for item in definition.items:
        values = [o.value for o in item.options]
        if item.input:
            if values or (item.max is not None and item.max < item.min):
                problems.append(f"пункт {item.id}: неверные границы числового поля или лишние варианты")
        elif not values or len(values) != len(set(values)):
            problems.append(f"пункт {item.id}: варианты ответа пусты или повторяются")
        if item.show_if:
            for c in item.show_if.conditions:
                if c.item not in earlier or not known[c.item].accepts(c.equals):
                    problems.append(f"пункт {item.id}: условие должно ссылаться на допустимый ответ предыдущего пункта")
        earlier.add(item.id)
    if problems:
        return problems

    def need(ids: Sequence[int | str], where: str) -> bool:
        if len(ids) != len(set(ids)):
            problems.append(f"{where}: пункты повторяются")
        for item_id in ids:
            if item_id not in known:
                problems.append(f"{where}: нет пункта {item_id}")
        return all(i in known for i in ids)

    def bounds(ids: Sequence[int | str], reverse: Sequence[int | str] = (), base: int = 0) -> tuple[float, float]:
        low = high = 0.0
        for i in ids:
            try:
                lo, hi = _value_range(definition, [i])
            except ValueError as exc:
                problems.append(f"пункт {i}: {exc}")
                continue
            if i in reverse:
                lo, hi = base - hi, base - lo
            low += lo
            high += hi
        return low, high

    def coverage(
        bands: Sequence[Band], low: float, high: float, where: str, continuous: bool = False, digits: int | None = None
    ) -> None:
        if not bands:
            return  # A raw score has no interpretation when the source supplies no thresholds.
        if len({b.code for b in bands}) != len(bands):
            problems.append(f"{where}: коды диапазонов повторяются")
        if digits is not None:
            factor = 10**digits
            scaled = [
                b.model_copy(update={"lower": round(b.lower * factor), "upper": round(b.upper * factor)}) for b in bands
            ]
            problems.extend(_check_integer_coverage(scaled, round(low * factor), round(high * factor), where))
        elif continuous:
            problems.extend(_check_halfopen_coverage(bands, low, high, where))
        else:
            problems.extend(_check_integer_coverage(bands, low, high, where))

    scoring = definition.scoring
    ids = scoring_ids(definition)
    if not need(ids, "расчёт"):
        return problems
    ranges: dict[str, tuple[float, float]] = {}
    for sub in getattr(scoring, "subscales", ()):
        if isinstance(sub, Subscale):
            refs = (*sub.direct, *sub.reverse)
            if need(refs, sub.code):
                dlow, dhigh = bounds(sub.direct)
                rlow, rhigh = bounds(sub.reverse)
                coverage(sub.bands, dlow - rhigh + sub.constant, dhigh - rlow + sub.constant, sub.code)
            continue
        if sub.code in ranges:
            problems.append(f"подшкала {sub.code}: код повторяется")
        if not need(sub.items, sub.code):
            continue
        if not set(sub.reverse) <= set(sub.items) or not set(sub.weights) <= set(sub.items):
            problems.append(f"подшкала {sub.code}: обратные пункты или веса вне подшкалы")
            continue
        low, high = bounds(sub.items, sub.reverse, sub.reverse_base)
        if sub.weights:
            if set(sub.weights) != set(sub.items):
                problems.append(f"подшкала {sub.code}: нет весов для всех пунктов")
            for item_id, weights in sub.weights.items():
                if set(weights) != {o.value for o in known[item_id].options}:
                    problems.append(f"подшкала {sub.code}: веса не покрывают ответы пункта {item_id}")
            low = sum(min(w.values()) for w in sub.weights.values() if w)
            high = sum(max(w.values()) for w in sub.weights.values() if w)
        if sub.aggregate == "mean":
            low, high = low / len(sub.items), high / len(sub.items)
        ranges[sub.code] = (low, high)
        if sub.norms:
            for group in (sub.norms.adults_and_young_men, sub.norms.young_women):
                if not group or [n.stanine for n in group] != list(range(1, 10)):
                    problems.append(f"подшкала {sub.code}: требуется 9 станайнов")
                elif group[0].lower > low or (group[-1].upper is not None and group[-1].upper < high):
                    problems.append(f"подшкала {sub.code}: нормы не покрывают возможные значения")
                for prev, nxt in itertools.pairwise(group):
                    if prev.upper is None or nxt.lower != prev.upper + 1:
                        problems.append(f"подшкала {sub.code}: нормы не стыкуются")

    try:
        match scoring:
            case SumScoring() | MeanScoring():
                if not ids or not set(scoring.reverse) <= set(ids):
                    problems.append("расчёт: пустые пункты или неверные обратные пункты")
                else:
                    low, high = bounds(ids, scoring.reverse, scoring.reverse_base)
                    if isinstance(scoring, MeanScoring):
                        low, high = low / len(ids), high / len(ids)
                    if scoring.bands_basis == "percent_of_max":
                        if high <= 0:
                            problems.append("процент от максимума: максимум должен быть положительным")
                        else:
                            low, high = low / high * 100, 100
                    coverage(scoring.bands, low, high, "диапазоны", isinstance(scoring, MeanScoring), scoring.round)
                    for name, bands in scoring.variants.items():
                        coverage(bands, low, high, name, isinstance(scoring, MeanScoring), scoring.round)
            case CountScoring():
                if not ids or any(scoring.value not in {o.value for o in known[i].options} for i in ids):
                    problems.append("счёт: значение отсутствует в вариантах ответа")
                if scoring.threshold is None:
                    if scoring.positive or scoring.negative:
                        problems.append("счёт: диапазоны без порога")
                elif not 0 < scoring.threshold <= len(ids) or not scoring.positive or not scoring.negative:
                    problems.append("счёт: неверный порог или отсутствуют диапазоны")
                else:
                    coverage((scoring.negative, scoring.positive), 0, len(ids), "диапазоны счёта")
                    if scoring.positive.lower != scoring.threshold:
                        problems.append("положительный диапазон должен начинаться с порога")
            case RuleScoring():
                for case in scoring.cases:
                    if not case.conditions:
                        problems.append("правило без условий")
                    for cond in case.conditions:
                        refs = (cond.item,) if cond.item is not None else cond.items
                        if not refs or (cond.item is not None and cond.items) or cond.count_min > len(refs):
                            problems.append("правило: неверный состав условия")
                        need(refs, f"правило {case.band.code}")
            case ConditionalBandsScoring():
                if need([scoring.selector_item], "пункт-переключатель"):
                    selector = known[scoring.selector_item]
                    if set(scoring.bands_by_selector) != {o.value for o in selector.options}:
                        problems.append("нет диапазонов для всех ответов пункта-переключателя")
                    low, high = bounds([i for i in ids if i != selector.id])
                    for bands in scoring.bands_by_selector.values():
                        coverage(bands, low, high, "диапазоны переключателя")
            case SingleScoring():
                if need([scoring.item], "итоговый пункт"):
                    low, high = bounds([scoring.item])
                    coverage(scoring.bands, low, high, "диапазоны")
            case ProfileScoring():
                for index in scoring.indices:
                    if index.code in ranges:
                        problems.append(f"индекс {index.code}: код повторяется")
                    if index.aggregate in {"sum_subscores", "mean_subscores"}:
                        if not index.subscales or any(k not in ranges for k in index.subscales):
                            problems.append(f"индекс {index.code}: неизвестные подшкалы")
                            continue
                        divisor = len(index.subscales) if index.aggregate == "mean_subscores" else 1
                        ranges[index.code] = (
                            sum(ranges[k][0] for k in index.subscales) / divisor,
                            sum(ranges[k][1] for k in index.subscales) / divisor,
                        )
                    elif index.aggregate == "count_positive":
                        ranges[index.code] = (0, len(ids))
                    else:
                        low, high = bounds(ids)
                        ranges[index.code] = (low / len(ids), high / len(ids))
                if scoring.bands_on is not None or scoring.bands:
                    if scoring.bands_on not in ranges:
                        problems.append("нет индекса для интерпретации")
                    else:
                        coverage(scoring.bands, *ranges[scoring.bands_on], "диапазоны профиля", True, scoring.round)
    except ValueError as exc:
        problems.append(str(exc))

    for rule in definition.critical:
        if isinstance(rule, ItemCritical):
            if need([rule.item], "критическое правило"):
                item = known[rule.item]
                top = item.max if item.input else max(o.value for o in item.options)
                if top is not None and rule.min_value > top:
                    problems.append(f"критическое правило: пункт {rule.item} не может достичь {rule.min_value}")
        elif isinstance(rule, CountCritical):
            need(rule.items, "критическое правило")
            if rule.count_min > len(rule.items):
                problems.append("критическое правило: недостижимый порог счёта")
        elif isinstance(rule, BandCritical):
            problems.extend(
                f"критическое правило: нет диапазона {c}" for c in rule.band_codes if c not in definition.band_codes()
            )
        elif isinstance(scoring, (NoScoring, RuleScoring, SubscaleScoring)):
            problems.append("критическое правило суммы без суммарного балла")
    return problems


def parse_scale(data: dict[str, Any], where: str = "шкала") -> ScaleDefinition:
    """Build and validate a definition from already-parsed YAML data.

    Resolves a top-level ``answer_sets`` mapping: an item may say ``answers: <set name>`` instead of
    listing ``options``.

    Raises:
        DefinitionError: schema errors or ``validate_definition`` problems (all listed in details).
    """
    data = dict(data)
    answer_sets = data.pop("answer_sets", {}) or {}
    items = []
    for raw in data.get("items", []) or []:
        item = dict(raw)
        if "answers" in item:
            name = item.pop("answers")
            if name not in answer_sets:
                raise DefinitionError(
                    f"{where}: пункт {item.get('id')} ссылается на неизвестный набор ответов {name!r}"
                )
            item["options"] = answer_sets[name]
        items.append(item)
    data["items"] = items
    definition = validate_model(ScaleDefinition, data, where)
    problems = validate_definition(definition)
    if problems:
        details = tuple(Problem(code="invalid_scale", message=p, pointer=definition.code) for p in problems)
        raise DefinitionError(f"{where}: ошибки в определении шкалы {definition.code}", details)
    return definition


def load_scale(path: Path) -> ScaleDefinition:
    """Read one YAML file and ``parse_scale`` it.

    Raises:
        DefinitionError: unreadable file, invalid YAML or invalid definition.
    """
    return parse_scale(read_yaml(path), where=path.name)


def load_scales(directory: Path) -> dict[str, ScaleDefinition]:
    """Load every ``*.yaml`` in ``directory``, keyed by scale code.

    Phase 1 holds one version per code; the repository (next phase) keys by (code, version) so old
    results re-render with the definition they were scored with.

    Raises:
        DefinitionError: any file is invalid, or two files declare the same code.
    """
    scales: dict[str, ScaleDefinition] = {}
    problems: list[Problem] = []
    for path in sorted(directory.glob("*.yaml")):
        try:
            definition = load_scale(path)
        except DefinitionError as exc:
            problems += [Problem(code=exc.code, message=exc.message, pointer=path.name), *exc.details]
            continue
        if definition.code in scales:
            problems.append(
                Problem(code="duplicate_scale", message=f"код {definition.code} повторяется", pointer=path.name)
            )
        scales[definition.code] = definition
    if problems:
        raise DefinitionError("Ошибки в файлах шкал", tuple(problems))
    return scales
