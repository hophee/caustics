"""Safety checks over the resulting therapy (current minus stopped, plus new).

Produces warnings the UI turns into coloured boxes (D-044, D-045):
* a conflict between two drugs is **one** warning targeting **both** items (worst severity of all
  matching rules, all sources kept), so both cards get a box and the hover names the other drug;
* a dose problem targets only the item's **dose** field;
* a patient-condition problem (pregnancy, kidneys, age, ...) targets the drug it concerns;
* NO_DATA entries are hover-only, never boxes (only yellow and red are boxes).

Nothing is filtered out (A05). Every warning cites its sources. All clinical policy comes from
``CheckRules`` and the interaction overrides (D-013). Checks are a pure function: cheap enough to
run on every basket change and again at confirmation.
"""

import hashlib
import itertools
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from enum import StrEnum

from kompas.core.errors import DefinitionError
from kompas.core.icd import IcdPattern
from kompas.core.text import normalize_name
from kompas.modules.cases.domain.profile import PatientProfile, Tristate
from kompas.modules.knowledge.check_rules import CheckRules, InteractionOverride, Outcome
from kompas.modules.knowledge.drugs import DrugLabel, DrugLookup, Fact, Status
from kompas.modules.knowledge.lab_rules import LabCheck, LabRules
from kompas.modules.labs.domain import LabValue, latest_by_test
from kompas.modules.therapy.domain.basket import CurrentTherapyItem, Draft, TherapyItem, resulting_therapy


class Severity(StrEnum):
    """Warning severity. Order with ``rank``; serialised as the string value."""

    NO_DATA = "no_data"  # grey, hover only: data missing — never read as "safe"
    CAUTION = "caution"  # yellow box
    CONTRAINDICATED = "contraindicated"  # red box; needs a justification on second confirm (D-046)

    @property
    def rank(self) -> int:
        """1, 2, 3."""
        return {"no_data": 1, "caution": 2, "contraindicated": 3}[self.value]

    @property
    def boxed(self) -> bool:
        """True for CAUTION and CONTRAINDICATED."""
        return self is not Severity.NO_DATA


class WarningKind(StrEnum):
    PATIENT_CONDITION = "patient_condition"  # pregnancy, lactation, age, ICD, kidneys, liver, ...
    MISSING_PATIENT_DATA = "missing_patient_data"  # patient fact unknown where the label cares
    INTOLERANCE = "intolerance"  # past intolerance/inefficacy, allergy, excipient
    INTERACTION = "interaction"  # pair of drugs
    GROUP_LOAD = "group_load"  # many drugs of one risky group together
    DUPLICATE = "duplicate"  # same substance twice
    DOSE = "dose"  # above the label maximum, or not verifiable
    UNVERIFIED_DATA = "unverified_data"  # label extraction flagged needs_check


class TargetPart(StrEnum):
    ITEM = "item"  # box the whole drug card
    DOSE = "dose"  # box only the dose field


@dataclass(frozen=True, slots=True)
class Target:
    """What to box in the UI: a card (``TherapyItem.key``) or its dose field."""

    item_key: str
    part: TargetPart


@dataclass(frozen=True, slots=True)
class Source:
    """Where a warning's reason comes from.

    ``document``: "ОХЛП", "КР", "медкарта", "правило группы", "проверено клинически" (override).
    ``ref``: label id or guideline row id, so the UI can link to the exact cause.
    """

    document: str
    ref: str | None = None
    section: str | None = None
    quote: str | None = None


@dataclass(frozen=True, slots=True)
class Warning:
    """One problem found by the checks.

    ``code`` is machine-readable (e.g. ``pregnancy_contraindicated``, ``interaction_group_contra``,
    ``dose_above_max``). ``id`` is deterministic from (kind, drug identities — label id or
    substance — of each party, label field), never from card keys or message text, so a justification
    stays attached across re-checks and between prescription versions.
    ``message`` is Russian hover text; for interactions it names the other drug and the reason.
    """

    id: str
    code: str
    kind: WarningKind
    severity: Severity
    targets: tuple[Target, ...]
    message: str
    sources: tuple[Source, ...]
    involves_new_item: bool


@dataclass(frozen=True, slots=True)
class CheckReport:
    """All warnings for one resulting therapy, plus the knowledge version they were computed with."""

    warnings: tuple[Warning, ...]  # worst first
    knowledge_version: str

    def for_item(self, item_key: str) -> tuple[Warning, ...]:
        """Warnings targeting ``item_key`` (any part): the hover content for that card."""
        return tuple(w for w in self.warnings if any(t.item_key == item_key for t in w.targets))

    def boxes(self) -> dict[Target, Severity]:
        """Worst *boxed* severity per target: the colour of each card / dose field."""
        result: dict[Target, Severity] = {}
        for warning in self.warnings:
            if not warning.severity.boxed:
                continue
            for target in warning.targets:
                current = result.get(target)
                if current is None or warning.severity.rank > current.rank:
                    result[target] = warning.severity
        return result

    def requiring_justification(self, previously_justified: frozenset[str]) -> tuple[Warning, ...]:
        """Red warnings needing a written justification: those involving a new item, plus red
        warnings on continued therapy that were not red-and-justified in the previous prescription.

        ``previously_justified`` must contain only ids of warnings that were **red and justified**
        in the previous version (``ConfirmedPrescription.justifications``), never all warning ids:
        a warning that escalates from yellow to red (new override, new diagnosis, pregnancy) then
        needs a new justification."""
        return tuple(
            w
            for w in self.warnings
            if w.severity is Severity.CONTRAINDICATED and (w.involves_new_item or w.id not in previously_justified)
        )


@dataclass(frozen=True, slots=True)
class CheckContext:
    """Everything the checks need besides the therapy itself.

    ``today`` is the patient's local date (``core.clock.local_date(now, patient.timezone)``).
    ``knowledge_version`` is the combined version of everything the warnings depend on (drug data,
    check rules, overrides, guidelines) — ``Knowledge.version`` — and is stored with every report.
    ``labs`` are the patient's demo lab values (A13), checked by ``lab_rules`` (none = no lab checks).
    """

    knowledge_version: str
    patient: PatientProfile
    drugs: DrugLookup
    rules: CheckRules
    overrides: Sequence[InteractionOverride]
    today: date
    labs: tuple[LabValue, ...] = ()
    lab_rules: LabRules | None = None


# --------------------------------------------------------------------------------------------------
# Internals
# --------------------------------------------------------------------------------------------------

_SEVERITY = {
    Outcome.NO_DATA: Severity.NO_DATA,
    Outcome.CAUTION: Severity.CAUTION,
    Outcome.CONTRAINDICATED: Severity.CONTRAINDICATED,
}

_STATUS_TEXT = {
    Status.CONTRAINDICATED: "противопоказано",
    Status.CAUTION: "с осторожностью",
    Status.DOSE_ADJUSTMENT: "нужна коррекция дозы",
    Status.NO_ADJUSTMENT: "без коррекции",
    Status.NOT_AFFECTED: "не влияет",
    Status.NO_DATA: "нет данных в инструкции",
}


@dataclass(frozen=True, slots=True)
class _Party:
    """A therapy item resolved against the catalog."""

    item: TherapyItem
    label: DrugLabel | None
    substances: tuple[str, ...]
    groups: frozenset[str]

    @property
    def identity(self) -> str:
        return self.label.id if self.label else f"substance:{self.substances[0] if self.substances else self.item.key}"

    @property
    def name(self) -> str:
        if self.label:
            return self.label.trade_name or ", ".join(self.substances)
        return self.item.substance or self.item.key


@dataclass(slots=True)
class _Acc:
    """Mutable accumulator for one warning id while merging."""

    code: str
    kind: WarningKind
    severity: Severity
    targets: list[Target]
    messages: list[str]
    sources: list[Source]
    involves_new: bool
    extra: dict[str, str] = field(default_factory=dict)


class _Collector:
    def __init__(self) -> None:
        self._acc: dict[str, _Acc] = {}

    def add(
        self,
        *,
        kind: WarningKind,
        code: str,
        severity: Severity | None,
        parties: Sequence[_Party],
        field_name: str,
        message: str,
        sources: Sequence[Source],
        part: TargetPart = TargetPart.ITEM,
    ) -> None:
        if severity is None:  # rule disabled in checks.yaml
            return
        identities = "|".join(sorted({p.identity for p in parties}))
        wid = hashlib.sha1(f"{kind}|{identities}|{field_name}".encode()).hexdigest()[:12]
        targets = [Target(p.item.key, part) for p in parties]
        involves_new = any(p.item.is_new for p in parties)
        acc = self._acc.get(wid)
        if acc is None:
            self._acc[wid] = _Acc(code, kind, severity, targets, [message], list(sources), involves_new)
            return
        if severity.rank > acc.severity.rank:
            acc.severity, acc.code = severity, code
        acc.targets += [t for t in targets if t not in acc.targets]
        if message not in acc.messages:
            acc.messages.append(message)
        acc.sources += [s for s in sources if s not in acc.sources]
        acc.involves_new = acc.involves_new or involves_new

    def warnings(self) -> tuple[Warning, ...]:
        items = [
            Warning(
                id=wid,
                code=a.code,
                kind=a.kind,
                severity=a.severity,
                targets=tuple(a.targets),
                message="; ".join(a.messages),
                sources=tuple(a.sources),
                involves_new_item=a.involves_new,
            )
            for wid, a in self._acc.items()
        ]
        items.sort(key=lambda w: (-w.severity.rank, w.kind, w.id))
        return tuple(items)


def _fixed(rules: CheckRules, key: str) -> Severity | None:
    """Severity of a fixed rule from ``checks.yaml: severities`` (None = rule disabled)."""
    outcome = rules.severities[key]
    return None if outcome is Outcome.NONE else _SEVERITY[outcome]


def _label_source(label: DrugLabel, fact: Fact[object] | None = None, section: str | None = None) -> Source:
    return Source(
        document="ОХЛП",
        ref=label.id,
        section=(fact.section if fact else None) or section,
        quote=fact.quote if fact else None,
    )


def _resolve(item: TherapyItem, drugs: DrugLookup) -> _Party:
    if item.label_id:
        label = drugs.get(item.label_id)
        return _Party(item, label, label.substances, label.groups)
    substances = tuple(normalize_name(s) for s in (item.substance or "").split(" + ") if s.strip())
    groups = frozenset().union(*(drugs.groups_of_substance(s) for s in substances))
    return _Party(item, None, substances, groups)


def _patient_fact(
    out: _Collector,
    party: _Party,
    rules: CheckRules,
    fact_value: Tristate,
    label_fact: Fact[Status],
    *,
    field_name: str,
    what: str,
    unknown_hint: str,
) -> None:
    """Apply the severity matrix to one (patient fact × label status) pair."""
    assert party.label is not None
    status = label_fact.value
    if fact_value is Tristate.YES:
        outcome = rules.matrix.if_yes[status]
        if outcome is Outcome.NONE:
            return
        kind = WarningKind.PATIENT_CONDITION
        message = f"{what}: {_STATUS_TEXT[status]} ({party.name})"
        code = f"{field_name}_{status.value}"
    elif fact_value is Tristate.UNKNOWN:
        outcome = rules.matrix.if_unknown[status]
        if outcome is Outcome.NONE:
            return
        kind = WarningKind.MISSING_PATIENT_DATA
        message = f"{unknown_hint}: в инструкции — {_STATUS_TEXT[status]} ({party.name})"
        code = f"{field_name}_unknown"
    else:
        return
    out.add(
        kind=kind,
        code=code,
        severity=_SEVERITY[outcome],
        parties=[party],
        field_name=field_name,
        message=message,
        sources=[_label_source(party.label, label_fact)],
    )


def _check_patient(out: _Collector, party: _Party, ctx: CheckContext) -> None:
    patient, rules = ctx.patient, ctx.rules
    label = party.label
    if label is None:
        out.add(
            kind=WarningKind.UNVERIFIED_DATA,
            code="label_unknown",
            severity=Severity.NO_DATA,
            parties=[party],
            field_name="label",
            message=f"{party.name}: нет инструкции (ОХЛП) в базе — проверки по пациенту не выполнены",
            sources=[Source("медкарта")],
        )
        return

    age = patient.age_on(ctx.today)
    low, high = rules.childbearing_age
    fertile = low <= age <= high

    def applicable(fact: Tristate) -> Tristate:
        # A recorded YES always counts; the age range only resolves an UNKNOWN (D-062).
        return Tristate.NO if fact is Tristate.UNKNOWN and not fertile else fact

    pregnant = applicable(patient.is_pregnant())
    lactating = applicable(patient.is_lactating())
    _patient_fact(
        out,
        party,
        rules,
        pregnant,
        label.pregnancy,
        field_name="pregnancy",
        what="Беременность",
        unknown_hint="Беременность не уточнена",
    )
    _patient_fact(
        out,
        party,
        rules,
        lactating,
        label.lactation,
        field_name="lactation",
        what="Грудное вскармливание",
        unknown_hint="Грудное вскармливание не уточнено",
    )
    if age >= rules.elderly_age:
        _patient_fact(
            out,
            party,
            rules,
            Tristate.YES,
            label.elderly,
            field_name="elderly",
            what=f"Возраст {age} лет",
            unknown_hint="",
        )
    if label.min_age.value is not None and age < label.min_age.value:
        out.add(
            kind=WarningKind.PATIENT_CONDITION,
            code="age_below_min",
            severity=_fixed(rules, "age_below_min"),
            parties=[party],
            field_name="min_age",
            message=f"Возраст {age} лет: препарат разрешён с {label.min_age.value} лет ({party.name})",
            sources=[_label_source(label, label.min_age)],
        )

    for raw in label.contraindicated_icd.value:
        try:
            pattern = IcdPattern.parse(raw)
        except DefinitionError:
            continue
        matched = patient.matching_diagnoses(pattern)
        if matched:
            out.add(
                kind=WarningKind.PATIENT_CONDITION,
                code="icd_contraindicated",
                severity=_fixed(rules, "icd_contraindicated"),
                parties=[party],
                field_name=f"icd:{pattern}",
                message=f"Противопоказан при {', '.join(map(str, matched))} ({party.name})",
                sources=[_label_source(label, label.contraindicated_icd, "4.3")],
            )

    for condition in rules.conditions:
        matched = tuple(code for raw in condition.icd for code in patient.matching_diagnoses(IcdPattern.parse(raw)))
        if matched:
            label_fact: Fact[Status] = getattr(label, condition.label_field)
            _patient_fact(
                out,
                party,
                rules,
                Tristate.YES,
                label_fact,
                field_name=condition.code,
                what=f"{condition.name.capitalize()} ({matched[0]})",
                unknown_hint="",
            )

    def listed(values: tuple[str, ...] | None) -> set[str]:
        return {normalize_name(v) for v in values or ()}

    if patient.allergies is None:
        if party.item.is_new:
            out.add(
                kind=WarningKind.MISSING_PATIENT_DATA,
                code="allergies_unknown",
                severity=Severity.NO_DATA,
                parties=[party],
                field_name="allergies",
                message="Аллергии не уточнены в медкарте",
                sources=[Source("медкарта")],
            )
    elif hit := listed(patient.allergies) & (
        set(label.substances) | set(label.excipients.value) | {normalize_name(label.trade_name)}
    ):
        out.add(
            kind=WarningKind.INTOLERANCE,
            code="allergy",
            severity=_fixed(rules, "allergy"),
            parties=[party],
            field_name="allergies",
            message=f"Аллергия на {', '.join(sorted(hit))} ({party.name})",
            sources=[Source("медкарта")],
        )
    if hit := listed(patient.past_intolerance) & (set(label.substances) | {normalize_name(label.trade_name)}):
        out.add(
            kind=WarningKind.INTOLERANCE,
            code="past_intolerance",
            severity=_fixed(rules, "past_intolerance"),
            parties=[party],
            field_name="past_intolerance",
            message=f"Ранее плохо переносил(а): {', '.join(sorted(hit))}",
            sources=[Source("медкарта")],
        )
    if hit := listed(patient.past_inefficacy) & set(label.substances):
        out.add(
            kind=WarningKind.INTOLERANCE,
            code="past_inefficacy",
            severity=_fixed(rules, "past_inefficacy"),
            parties=[party],
            field_name="past_inefficacy",
            message=f"Ранее был(а) неэффективен: {', '.join(sorted(hit))}",
            sources=[Source("медкарта")],
        )
    if patient.intolerant_excipients is None:
        if party.item.is_new and label.excipients.value:
            out.add(
                kind=WarningKind.MISSING_PATIENT_DATA,
                code="excipients_unknown",
                severity=Severity.NO_DATA,
                parties=[party],
                field_name="excipients",
                message=f"Непереносимость вспомогательных веществ не уточнена; в составе: "
                f"{', '.join(label.excipients.value)}",
                sources=[_label_source(label, label.excipients)],
            )
    elif hit := listed(patient.intolerant_excipients) & set(label.excipients.value):
        out.add(
            kind=WarningKind.INTOLERANCE,
            code="excipient",
            severity=_fixed(rules, "excipient"),
            parties=[party],
            field_name="excipients",
            message=f"Непереносимость: {', '.join(sorted(hit))} в составе {party.name}",
            sources=[_label_source(label, label.excipients)],
        )

    for rule in rules.patient_level:
        if rule.group in label.patient_level_contraindicated.value:
            status, level_fact = Status.CONTRAINDICATED, label.patient_level_contraindicated
        elif rule.group in label.patient_level_caution.value:
            status, level_fact = Status.CAUTION, label.patient_level_caution
        else:
            continue
        fact = getattr(patient, rule.profile_fact, Tristate.UNKNOWN)
        if fact is Tristate.YES:
            outcome, code = rules.matrix.if_yes[status], f"patient_level_{rule.profile_fact}"
        elif fact is Tristate.UNKNOWN:
            outcome, code = rules.matrix.if_unknown[status], f"patient_level_{rule.profile_fact}_unknown"
        else:
            continue
        if outcome is Outcome.NONE:
            continue
        out.add(
            kind=WarningKind.PATIENT_CONDITION,
            code=code,
            severity=_SEVERITY[outcome],
            parties=[party],
            field_name=f"patient_level:{rule.group}",
            message=f"{rule.message}: {_STATUS_TEXT[status]} ({party.name})",
            sources=[_label_source(label, level_fact, "4.5")],
        )

    if label.needs_check:
        out.add(
            kind=WarningKind.UNVERIFIED_DATA,
            code="label_needs_check",
            severity=Severity.NO_DATA,
            parties=[party],
            field_name="needs_check",
            message=f"Данные ОХЛП {party.name} требуют сверки: {label.needs_check_reason or 'см. инструкцию'}",
            sources=[_label_source(label)],
        )


def _check_doses(out: _Collector, parties: Sequence[_Party], rules: CheckRules) -> None:
    """Dose checks over the whole resulting therapy.

    Doses of the same substance are added up across items (current, patient-reported and new) and
    compared with the strictest parsed label maximum among them, so two moderate prescriptions of
    one drug can't add up to an unnoticed overdose. Items whose dose can't be verified (combination
    label, maximum not parsed) get a grey dose entry when they are new.
    """
    severity = _fixed(rules, "dose_above_max")
    by_substance: dict[str, list[_Party]] = {}
    for party in parties:
        label, regimen = party.label, party.item.regimen
        if label is not None and party.item.is_new and regimen is not None:
            if label.is_combination:
                out.add(
                    kind=WarningKind.DOSE,
                    code="dose_not_verifiable",
                    severity=Severity.NO_DATA,
                    parties=[party],
                    field_name="dose",
                    part=TargetPart.DOSE,
                    message="Дозу комбинированного препарата нельзя проверить автоматически",
                    sources=[_label_source(label, label.dose.max_text)],
                )
            elif label.dose.max_daily_mg is None:
                out.add(
                    kind=WarningKind.DOSE,
                    code="dose_not_verifiable",
                    severity=Severity.NO_DATA,
                    parties=[party],
                    field_name="dose",
                    part=TargetPart.DOSE,
                    message="Максимальная суточная доза в ОХЛП не распознана — сверьте по цитате",
                    sources=[_label_source(label, label.dose.max_text, "4.2")],
                )
        if regimen is not None and len(party.substances) == 1:
            by_substance.setdefault(party.substances[0], []).append(party)

    for substance, group in sorted(by_substance.items()):
        limits = [
            (p.label.dose.max_daily_mg, p.label)
            for p in group
            if p.label is not None and not p.label.is_combination and p.label.dose.max_daily_mg is not None
        ]
        if not limits or severity is None:
            continue
        maximum, source_label = min(limits, key=lambda t: t[0])
        assert maximum is not None
        total = sum((p.item.regimen.max_daily_mg for p in group if p.item.regimen), Decimal(0))
        if total <= maximum:
            continue
        if len(group) == 1:
            message = f"Суточная доза {total:g} мг больше максимальной по инструкции ({maximum:g} мг)"
        else:
            parts = " + ".join(f"{p.name} {p.item.regimen.max_daily_mg:g} мг" for p in group if p.item.regimen)
            message = f"{substance}: суммарно {total:g} мг/сут ({parts}) больше максимальной ({maximum:g} мг)"
        out.add(
            kind=WarningKind.DOSE,
            code="dose_above_max",
            severity=severity,
            parties=group,
            field_name=f"dose:{substance}",
            part=TargetPart.DOSE,
            message=message,
            sources=[_label_source(source_label, source_label.dose.max_text, "4.2")],
        )


def _override_for(a: str, b: str, overrides: Sequence[InteractionOverride]) -> InteractionOverride | None:
    pair = {a, b}
    for override in overrides:
        if set(override.substances) == pair:
            return override
    return None


def _check_pair(out: _Collector, a: _Party, b: _Party, ctx: CheckContext) -> None:
    if a.substances and set(a.substances) == set(b.substances):
        return  # same drug twice: reported once as DUPLICATE (and summed in the dose check)
    findings: list[tuple[Severity, str, str, Source]] = []
    for x, y in ((a, b), (b, a)):
        if x.label is None:
            continue
        hard = x.label.contraindicated_with.value & y.groups
        if hard:
            findings.append(
                (
                    Severity.CONTRAINDICATED,
                    "interaction_group_contra",
                    f"нельзя вместе: {', '.join(sorted(hard))} (инструкция {x.name})",
                    _label_source(x.label, x.label.contraindicated_with, "4.5"),
                )
            )
        soft = (x.label.caution_with.value & y.groups) - hard
        if soft:
            findings.append(
                (
                    Severity.CAUTION,
                    "interaction_group_caution",
                    f"с осторожностью: {', '.join(sorted(soft))} (инструкция {x.name})",
                    _label_source(x.label, x.label.caution_with, "4.5"),
                )
            )
        named = set(x.label.named_interactions.value) & set(y.substances)
        for substance in sorted(named):
            override = next((o for s in x.substances if (o := _override_for(s, substance, ctx.overrides))), None)
            outcome = override.outcome if override else ctx.rules.named_interaction_default
            if outcome is Outcome.NONE:
                continue
            code = "interaction_named_reviewed" if override else "interaction_named_unreviewed"
            text = (
                f"{override.note}"
                if override
                else f"{substance} указан в разделе 4.5 инструкции {x.name} — оцените по цитате"
            )
            findings.append((_SEVERITY[outcome], code, text, _label_source(x.label, x.label.named_interactions, "4.5")))
    for sa in a.substances:
        for sb in b.substances:
            override = _override_for(sa, sb, ctx.overrides)
            if override and override.outcome is not Outcome.NONE:
                findings.append(
                    (
                        _SEVERITY[override.outcome],
                        "interaction_named_reviewed",
                        override.note,
                        Source("проверено клинически", None, None, override.note),
                    )
                )
    if not findings:
        return
    worst = max(findings, key=lambda f: f[0].rank)
    reasons = list(dict.fromkeys(f[2] for f in findings))
    sources = list(dict.fromkeys(f[3] for f in findings))
    out.add(
        kind=WarningKind.INTERACTION,
        code=worst[1],
        severity=worst[0],
        parties=[a, b],
        field_name="interaction",
        message=f"{a.name} + {b.name}: " + "; ".join(reasons),
        sources=sources,
    )


def _check_whole(out: _Collector, parties: Sequence[_Party], rules: CheckRules) -> None:
    by_substance: dict[str, list[_Party]] = {}
    for party in parties:
        for substance in party.substances:
            by_substance.setdefault(substance, []).append(party)
    for substance, group in sorted(by_substance.items()):
        if len(group) > 1:
            out.add(
                kind=WarningKind.DUPLICATE,
                code="duplicate_substance",
                severity=_fixed(rules, "duplicate_substance"),
                parties=group,
                field_name=f"duplicate:{substance}",
                message=f"{substance} назначен(о) дважды: {', '.join(p.name for p in group)}",
                sources=[Source("правило группы")],
            )
    for rule in rules.group_load:
        members = [p for p in parties if rule.group in p.groups]
        if len(members) >= rule.min_count and rule.outcome is not Outcome.NONE:
            out.add(
                kind=WarningKind.GROUP_LOAD,
                code=f"group_load:{rule.group}",
                severity=_SEVERITY[rule.outcome],
                parties=members,
                field_name=f"group_load:{rule.group}",
                message=f"{rule.message}: {', '.join(p.name for p in members)}",
                sources=[Source("правило группы")],
            )


_NEUTRAL = frozenset({Status.NO_ADJUSTMENT, Status.NOT_AFFECTED, Status.NO_DATA})
_FIXED_OUTCOME = {"caution": Severity.CAUTION, "contraindicated": Severity.CONTRAINDICATED}


def _lab_severity(check: LabCheck, party: _Party, rules: CheckRules) -> tuple[bool, Severity | None, Source | None]:
    """Whether ``check`` concerns this drug, the severity if its threshold is crossed, and the label
    source behind it (for label-field selectors)."""
    if check.drugs.groups:
        if not set(check.drugs.groups) & party.groups:
            return False, None, None
        return True, _FIXED_OUTCOME.get(check.outcome), None
    if party.label is None or check.drugs.label_field is None:
        return False, None, None
    fact: Fact[Status] = getattr(party.label, check.drugs.label_field)
    if fact.value in _NEUTRAL:
        return False, None, None
    source = _label_source(party.label, fact)
    if check.outcome == "from_label":
        outcome = rules.matrix.if_yes[fact.value]
        return outcome is not Outcome.NONE, _SEVERITY.get(outcome), source
    return True, _FIXED_OUTCOME[check.outcome], source


def _check_labs(out: _Collector, party: _Party, ctx: CheckContext) -> None:
    """Demo lab values (A13) × the drug: the latest value of a test, if fresh, against the thresholds of
    ``labs.yaml``; a missing or stale value never counts as normal (grey note where configured)."""
    lab_rules = ctx.lab_rules
    if lab_rules is None:
        return
    latest = latest_by_test(ctx.labs)
    age = ctx.patient.age_on(ctx.today)
    for check in lab_rules.checks:
        if check.min_age is not None and age < check.min_age:
            continue
        concerns, severity, label_source = _lab_severity(check, party, ctx.rules)
        if not concerns:
            continue
        test = lab_rules.test(check.test)
        assert test is not None  # validated at load
        value = latest.get(check.test)
        if value is None or (ctx.today - value.measured_on).days > lab_rules.stale_after_days:
            if check.when_missing == "no_data":
                last = f"; последнее — {value.measured_on:%d.%m.%Y}" if value else ""
                out.add(
                    kind=WarningKind.MISSING_PATIENT_DATA,
                    code=f"lab_missing:{check.code}",
                    severity=Severity.NO_DATA,
                    parties=[party],
                    field_name=f"lab:{check.code}",
                    message=f"{test.label}: нет свежих данных (демо-анализы{last}) ({party.name})",
                    sources=[Source("демо-анализ", ref=check.code)],
                )
            continue
        sex = ctx.patient.sex.value
        if not check.crossed(value.value, sex):
            continue
        shown = f"{test.label} {value.value:g} {test.unit} ({check.threshold_text(sex)}), {value.measured_on:%d.%m.%Y}"
        out.add(
            kind=WarningKind.PATIENT_CONDITION,
            code=f"lab:{check.code}",
            severity=severity,
            parties=[party],
            field_name=f"lab:{check.code}",
            message=f"{check.message}: {shown} — демо-данные ({party.name})",
            sources=[Source("демо-анализ", ref=check.code, quote=shown), *([label_source] if label_source else [])],
        )


def check_basket(current: tuple[CurrentTherapyItem, ...], draft: Draft, ctx: CheckContext) -> CheckReport:
    """Run every check on ``resulting_therapy(current, draft)`` and return all warnings, worst first.

    Per item vs patient (via ``rules.matrix``; patient NO → nothing, UNKNOWN → MISSING_PATIENT_DATA):
    pregnancy, lactation; age below ``min_age``; elderly (``rules.elderly_age``); 4.3 ICD patterns;
    renal/hepatic conditions (``rules.conditions``); allergies, past intolerance/inefficacy,
    excipients (None = not asked → MISSING_PATIENT_DATA); patient-level groups such as «алкоголь»
    (``rules.patient_level``); ``needs_check`` labels → UNVERIFIED_DATA; demo lab values against
    ``ctx.lab_rules`` (A13; a missing/stale value → NO_DATA where the rule asks for it).
    Doses: same-substance doses are summed across all items and compared with the strictest parsed
    label maximum → DOSE on every involved dose field; unverifiable new items → NO_DATA (hover only).
    Per pair: group contra/caution in either direction, named МНН in 4.5 (default
    ``rules.named_interaction_default``, overridden by ``overrides``); merged into one warning per
    pair. Whole therapy: same substance twice → DUPLICATE; ``rules.group_load``.

    Warning identity: one id per (kind, set of drug identities, label field). Warnings with equal
    ids are merged (targets combined, worst severity, all sources; ``code`` = worst rule's code).
    Multi-substance labels and current drugs known only by name get a NO_DATA entry where a check
    can't be done (dose, label facts) instead of being skipped silently.
    An UNKNOWN pregnancy/lactation is treated as not applicable outside ``rules.childbearing_age``
    (as for males); a recorded YES always counts. Fixed-rule severities come from ``rules.severities``.

    Known limitations (phase 1): renal/hepatic conditions come only from recorded diagnoses, so they
    are never "unknown"; КР restrictions (e.g. mixed features) are shown during selection only and
    not re-checked here.

    Raises:
        InvalidInputError: the draft is structurally invalid.
        NotFoundError: a label id is unknown.
    """
    items = resulting_therapy(current, draft)
    parties = [_resolve(item, ctx.drugs) for item in items]
    out = _Collector()
    for party in parties:
        _check_patient(out, party, ctx)
        _check_labs(out, party, ctx)
    _check_doses(out, parties, ctx.rules)
    for a, b in itertools.combinations(parties, 2):
        _check_pair(out, a, b, ctx)
    _check_whole(out, parties, ctx.rules)
    return CheckReport(warnings=out.warnings(), knowledge_version=ctx.knowledge_version)
