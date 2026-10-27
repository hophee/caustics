"""Configurable drug-check policy: ``knowledge_data/rules/checks.yaml`` and
``knowledge_data/rules/interaction_overrides.yaml``.

Everything clinical that the checks decide — which ICD codes mean "kidney disease", the elderly
age, how a label status combines with a patient fact, group-load limits, the minimum justification
length, reviewed severities for named interactions — lives here, not in code (D-013).
"""

from enum import StrEnum
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from kompas.core.errors import DefinitionError
from kompas.core.icd import IcdPattern
from kompas.core.text import normalize_name
from kompas.modules.knowledge._files import read_yaml, validate_model
from kompas.modules.knowledge.drugs import Status


class Outcome(StrEnum):
    """Result of combining a patient fact with a label status (see ``SeverityMatrix``)."""

    NONE = "none"  # no warning
    NO_DATA = "no_data"  # grey, hover only
    CAUTION = "caution"  # yellow
    CONTRAINDICATED = "contraindicated"  # red


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SeverityMatrix(_Model):
    """Outcome per label status when the patient fact is YES, and when it is UNKNOWN.

    When the patient fact is NO, there is never a warning. When it is UNKNOWN, the warning kind is
    MISSING_PATIENT_DATA ("уточните беременность") with the outcome from ``if_unknown``.
    """

    if_yes: dict[Status, Outcome]
    if_unknown: dict[Status, Outcome]


class ConditionRule(_Model):
    """Which ICD patterns count as a condition that a label field speaks about.

    Example: ``code: renal``, ``icd: [N17, N18, N19]``, ``label_field: renal``.
    """

    code: str
    name: str  # Russian, used in messages: «почечная недостаточность»
    icd: tuple[str, ...]
    label_field: str  # one of: renal, hepatic


class GroupLoadRule(_Model):
    """``min_count`` or more drugs of ``group`` together produce a warning with ``outcome``."""

    group: str
    min_count: int
    outcome: Outcome
    message: str


class PatientLevelRule(_Model):
    """Maps a patient-level pseudo-group (e.g. «алкоголь») to a profile fact."""

    group: str
    profile_fact: str  # e.g. "substance_use"
    message: str


class GroupMembership(_Model):
    """Substances / ATC prefixes that belong to an interaction group regardless of what the label
    extraction says (e.g. pirlindol and moclobemide are MAO inhibitors). Applied to labels and to
    the substance dictionary at import."""

    group: str
    substances: tuple[str, ...] = ()
    atc_prefixes: tuple[str, ...] = ()
    note: str = ""


SEVERITY_KEYS = frozenset(
    {
        "age_below_min",
        "icd_contraindicated",
        "allergy",
        "excipient",
        "past_intolerance",
        "past_inefficacy",
        "dose_above_max",
        "duplicate_substance",
    }
)


class CheckRules(_Model):
    """All check policy for one knowledge version."""

    matrix: SeverityMatrix
    severities: dict[str, Outcome]  # outcome per fixed rule (keys: SEVERITY_KEYS)
    interaction_groups: tuple[str, ...]  # drug-level vocabulary used in ОХЛП 4.5 extraction
    group_membership: tuple[GroupMembership, ...] = ()
    elderly_age: int
    childbearing_age: tuple[int, int]  # outside this age range pregnancy/lactation are not applicable
    conditions: tuple[ConditionRule, ...]
    group_load: tuple[GroupLoadRule, ...]
    patient_level: tuple[PatientLevelRule, ...]
    named_interaction_default: Outcome
    justification_min_length: int


class InteractionOverride(_Model):
    """A clinically reviewed severity for a named substance pair (order-independent)."""

    substances: tuple[str, str]
    outcome: Outcome
    note: str
    reviewed_by: str | None = None


def load_check_rules(path: Path) -> CheckRules:
    """Parse ``checks.yaml``; ICD patterns are validated with ``IcdPattern.parse``.

    Raises:
        DefinitionError: malformed file or invalid ICD pattern.
    """
    rules = validate_model(CheckRules, read_yaml(path), path.name)
    for condition in rules.conditions:
        for pattern in condition.icd:
            IcdPattern.parse(pattern)
        if condition.label_field not in ("renal", "hepatic"):
            raise DefinitionError(f"{path.name}: неизвестное поле ОХЛП {condition.label_field!r}")
    for matrix in (rules.matrix.if_yes, rules.matrix.if_unknown):
        missing = set(Status) - set(matrix)
        if missing:
            raise DefinitionError(f"{path.name}: в матрице нет статусов {sorted(missing)}")
    low, high = rules.childbearing_age
    if low >= high:
        raise DefinitionError(f"{path.name}: некорректный childbearing_age")
    missing_keys = SEVERITY_KEYS - set(rules.severities)
    if missing_keys:
        raise DefinitionError(f"{path.name}: в severities нет {sorted(missing_keys)}")
    for membership in rules.group_membership:
        if membership.group not in rules.interaction_groups:
            raise DefinitionError(f"{path.name}: group_membership ссылается на неизвестную группу {membership.group!r}")
    return rules


def load_interaction_overrides(path: Path) -> tuple[InteractionOverride, ...]:
    """Parse ``interaction_overrides.yaml``; substance names are normalised.

    Raises:
        DefinitionError: malformed file or the same pair listed twice.
    """
    data = read_yaml(path).get("overrides") or []
    result: list[InteractionOverride] = []
    seen: set[frozenset[str]] = set()
    for i, raw in enumerate(data):
        item = validate_model(InteractionOverride, raw, f"{path.name}[{i}]")
        a, b = (normalize_name(x) for x in item.substances)
        pair = frozenset((a, b))
        if pair in seen:
            raise DefinitionError(f"{path.name}: пара {a} + {b} указана дважды")
        seen.add(pair)
        result.append(item.model_copy(update={"substances": (a, b)}))
    return tuple(result)
