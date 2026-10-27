"""Guideline-based suggestions for the basket (spec §6; D-041, D-042).

diagnosis → every guideline row of the diagnosis → classes / ATC rules → МНН → concrete labels.
The therapy stage / line is not asked and not used to filter (D-122): each row keeps its КР condition
text for the doctor to read. Suicide risk and pregnancy are read from the patient profile (for
restriction triggers).
Candidates are suggestions only: they carry their row and are checked like any other item.

Rows whose drug list could not be fully parsed (``text_only`` or non-empty ``unparsed``) are never
dropped silently: their text is returned as advice with the row's source, and a drug that might sit
in such a fragment is reported as ``KrStatus.UNVERIFIED`` rather than "not mentioned".
"""

from dataclasses import dataclass
from enum import StrEnum

from kompas.core.errors import NotFoundError
from kompas.core.icd import IcdCode, IcdPattern
from kompas.core.text import normalize_name
from kompas.modules.cases.domain.profile import PatientProfile, SuicideRisk, Tristate
from kompas.modules.knowledge.drugs import DrugCatalog, DrugLabel
from kompas.modules.knowledge.guidelines import (
    ClassTarget,
    GuidelineRow,
    Guidelines,
    Restriction,
    SubstanceTarget,
    WindowTarget,
)
from kompas.modules.therapy.domain.basket import KrSuggestion, Origin
from kompas.modules.therapy.domain.selection_form import SelectionAnswers, filter_rows


@dataclass(frozen=True, slots=True)
class Candidate:
    """A suggested label with the guideline row that suggests it."""

    label: DrugLabel
    row: GuidelineRow
    off_label_in_kr: bool
    evidence: str


@dataclass(frozen=True, slots=True)
class Advice:
    """Guideline text that is not a concrete drug (or could not be parsed); shown verbatim."""

    row: GuidelineRow
    text: str


@dataclass(frozen=True, slots=True)
class Selection:
    """Everything the selection screen shows."""

    candidates: tuple[Candidate, ...]
    advice: tuple[Advice, ...]
    restrictions: tuple[Restriction, ...]
    rows: tuple[GuidelineRow, ...]
    restrictions_to_clarify: tuple[Restriction, ...] = ()  # apply if an unknown patient fact is true


class KrStatus(StrEnum):
    """How the guidelines relate to a drug for this diagnosis (D-042)."""

    RECOMMENDED = "recommended"
    UNVERIFIED = "unverified"  # a matching row has text we couldn't parse: «проверьте по КР»
    NOT_MENTIONED = "not_mentioned"


@dataclass(frozen=True, slots=True)
class Provenance:
    """Server-derived provenance stored with a confirmed item (never taken from the client)."""

    kr_status: KrStatus
    label_indicated: Tristate  # from 4.1 indications vs diagnosis; UNKNOWN if not decidable
    row: GuidelineRow | None
    off_label_in_kr: bool = False


def diagnosis_rows(diagnosis: IcdCode, guidelines: Guidelines) -> tuple[GuidelineRow, ...]:
    """Every guideline row of the diagnosis, in guideline order.

    Raises:
        NotFoundError: the diagnosis is not among the guideline diagnoses.
    """
    rows = tuple(row for row in guidelines.rows if row.code == diagnosis.code)
    if not rows:
        matches = [code for code in guidelines.diagnoses if IcdPattern.parse(code).matches(diagnosis)]
        if matches:
            key = max(matches, key=len)
            rows = tuple(row for row in guidelines.rows if row.code == key)
    if not rows and diagnosis.code not in guidelines.diagnoses:
        raise NotFoundError(f"Для диагноза {diagnosis} нет клинических рекомендаций в базе")
    return rows


def _substances_of(row: GuidelineRow, guidelines: Guidelines) -> dict[str, tuple[bool, str | None]]:
    """Normalised МНН named by a row (directly or via classes) → (off_label, evidence)."""
    result: dict[str, tuple[bool, str | None]] = {}
    for target in row.targets:
        if isinstance(target, SubstanceTarget):
            result[target.mnn] = (target.off_label, target.evidence)
        elif isinstance(target, ClassTarget):
            drug_class = guidelines.class_named(target.name)
            for mnn in drug_class.substances if drug_class else ():
                result.setdefault(mnn, (False, target.evidence))
    return result


def _window_labels(target: WindowTarget, guidelines: Guidelines, catalog: DrugCatalog) -> list[DrugLabel]:
    rule = guidelines.window_rule(target.window)
    if rule is None:
        return []
    labels = [
        label
        for label in catalog.labels()
        if label.atc and any(label.atc.startswith(prefix) for prefix in rule.atc_prefixes)
    ]
    labels += [label for mnn in rule.add_substances for label in catalog.by_substance(mnn)]
    excluded = set(rule.exclude_substances)
    return [label for label in labels if not (set(label.substances) & excluded)]


def _restriction_applies(
    restriction: Restriction, diagnosis: IcdCode, patient: PatientProfile, answers: SelectionAnswers | None = None
) -> Tristate:
    """YES = applies; UNKNOWN = would apply if an unknown fact is true (show «уточните»);
    NO = does not apply. Missing data is never read as "no risk". Mixed features and the stage are
    no longer asked (D-122), so restrictions that depend on them are always UNKNOWN."""
    if not any(IcdPattern.parse(p).matches(diagnosis) for p in restriction.icd_patterns):
        return Tristate.NO
    t = restriction.trigger
    checks: list[Tristate] = []

    def known(value: bool) -> Tristate:
        return Tristate.YES if value else Tristate.NO

    if t.mixed_features is not None:
        checks.append(
            known(any("смешан" in flag.casefold() for flag in answers.flags))
            if answers is not None
            else Tristate.UNKNOWN
        )
    if t.suicide_risk is not None:
        risk = patient.suicide_risk
        checks.append(Tristate.UNKNOWN if risk is SuicideRisk.UNKNOWN else known(risk is SuicideRisk(t.suicide_risk)))
    if t.pregnant is not None:
        pregnant = patient.is_pregnant()
        checks.append(
            Tristate.UNKNOWN if pregnant is Tristate.UNKNOWN else known((pregnant is Tristate.YES) == t.pregnant)
        )
    if t.stage_contains is not None:
        checks.append(
            known(t.stage_contains in answers.choice) if answers is not None and answers.choice else Tristate.UNKNOWN
        )
    if not checks:
        return Tristate.YES if t.always else Tristate.UNKNOWN
    if Tristate.NO in checks:
        return Tristate.NO
    return Tristate.UNKNOWN if Tristate.UNKNOWN in checks else Tristate.YES


def select_candidates(
    diagnosis: IcdCode,
    guidelines: Guidelines,
    catalog: DrugCatalog,
    patient: PatientProfile,
    answers: SelectionAnswers | None = None,
) -> Selection:
    """All rows of the diagnosis expanded to labels; plus advice (text-only rows and unparsed
    fragments, with their row) and the restrictions that apply (shown as warnings, never as hiding).

    Raises:
        NotFoundError: the diagnosis is not among the guideline diagnoses.
    """
    rows = diagnosis_rows(diagnosis, guidelines)
    if answers is not None:
        rows = filter_rows(diagnosis, rows, answers)
    candidates: dict[tuple[str, str], Candidate] = {}
    advice: list[Advice] = []
    for row in rows:
        for mnn, (off_label, evidence) in _substances_of(row, guidelines).items():
            for label in catalog.by_substance(mnn):
                candidates.setdefault((label.id, row.id), Candidate(label, row, off_label, evidence or row.evidence))
        for target in row.targets:
            if isinstance(target, WindowTarget):
                for label in _window_labels(target, guidelines, catalog):
                    candidates.setdefault((label.id, row.id), Candidate(label, row, False, row.evidence))
        if row.text_only:
            advice.append(Advice(row, row.substances_text))
        else:
            advice.extend(Advice(row, fragment) for fragment in row.unparsed)
    verdicts = [(r, _restriction_applies(r, diagnosis, patient, answers)) for r in guidelines.restrictions]
    applies = tuple(r for r, v in verdicts if v is Tristate.YES)
    to_clarify = tuple(r for r, v in verdicts if v is Tristate.UNKNOWN)
    return Selection(tuple(candidates.values()), tuple(advice), applies, rows, to_clarify)


def _row_covers(row: GuidelineRow, label: DrugLabel, guidelines: Guidelines) -> tuple[bool, bool]:
    """(row recommends this label, off-label flag) — via substances, classes or role windows."""
    named = _substances_of(row, guidelines)
    hit = set(label.substances) & set(named)
    if hit:
        return True, any(named[m][0] for m in hit)
    for target in row.targets:
        if isinstance(target, WindowTarget):
            rule = guidelines.window_rule(target.window)
            if rule is None or set(label.substances) & set(rule.exclude_substances):
                continue
            by_atc = bool(label.atc) and any(label.atc.startswith(p) for p in rule.atc_prefixes)  # type: ignore[union-attr]
            if by_atc or set(label.substances) & set(rule.add_substances):
                return True, False
    return False, False


def _label_indicated(label: DrugLabel, diagnosis: IcdCode, guidelines: Guidelines) -> Tristate:
    text = normalize_name(label.indications.value or "")
    keywords = guidelines.vocabulary.indication_keywords.get(diagnosis.category)
    if not text or not keywords:
        return Tristate.UNKNOWN
    return Tristate.YES if any(k in text for k in keywords) else Tristate.UNKNOWN


def provenance(
    origin: Origin,
    label: DrugLabel,
    diagnosis: IcdCode,
    guidelines: Guidelines,
) -> Provenance:
    """Resolve provenance for a draft item on the server.

    A suggested row is preferred, and ignored if it belongs to another diagnosis. A drug named by
    any row of the diagnosis (directly, via a class or via a role window) is RECOMMENDED (with that
    row). Otherwise, if a row of
    the diagnosis has unparsed or text-only content, the status is UNVERIFIED (with that row), never
    NOT_MENTIONED. ``label_indicated`` is YES when the 4.1 text names the condition, else UNKNOWN.

    Raises:
        NotFoundError: ``origin`` references an unknown guideline row.
    """
    indicated = _label_indicated(label, diagnosis, guidelines)
    try:
        rows = list(diagnosis_rows(diagnosis, guidelines))
    except NotFoundError:
        rows = []
    if isinstance(origin, KrSuggestion):
        suggested = guidelines.row(origin.row_id)
        if any(row.id == suggested.id for row in rows):  # never trust another diagnosis
            rows = [suggested, *[r for r in rows if r.id != suggested.id]]
    for row in rows:
        covered, off_label = _row_covers(row, label, guidelines)
        if covered:
            return Provenance(KrStatus.RECOMMENDED, indicated, row, off_label)
    unclear = next((row for row in rows if row.text_only or row.unparsed), None)
    if unclear is not None:
        return Provenance(KrStatus.UNVERIFIED, indicated, unclear)
    return Provenance(KrStatus.NOT_MENTIONED, indicated, None)
