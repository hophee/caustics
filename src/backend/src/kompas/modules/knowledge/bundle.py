"""Load all knowledge of one version at once (scales, complaints, rules, guidelines, drugs)."""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kompas.core.errors import DefinitionError, Problem
from kompas.core.text import normalize_name
from kompas.modules.knowledge._files import files_digest
from kompas.modules.knowledge.care_rules import (
    AdherenceRules,
    AdverseEventRules,
    AppointmentRules,
    DocumentRules,
    ReferralRules,
    load_adherence_rules,
    load_adverse_event_rules,
    load_appointment_rules,
    load_document_rules,
    load_referral_rules,
)
from kompas.modules.knowledge.check_rules import (
    CheckRules,
    InteractionOverride,
    load_check_rules,
    load_interaction_overrides,
)
from kompas.modules.knowledge.complaints import ComplaintCatalog, load_complaints
from kompas.modules.knowledge.drugs import DrugCatalog, load_catalog_from_csv
from kompas.modules.knowledge.guidelines import Guidelines, load_guidelines
from kompas.modules.knowledge.lab_rules import LabRules, load_lab_rules
from kompas.modules.knowledge.scales import ScaleDefinition, load_scales
from kompas.modules.knowledge.triage_rules import TriageRules, load_triage_rules


@dataclass(frozen=True, slots=True)
class Knowledge:
    """Everything loaded from ``knowledge_data/`` (+ the drug CSV, if given).

    ``drugs`` is None when no drug CSV was provided (scales and triage still work).
    ``import_warnings`` collects non-fatal problems found while importing drug data and guidelines.
    ``version`` combines the versions of every component (stored with check reports, D-013).
    """

    version: str
    scales: dict[str, ScaleDefinition]
    complaints: ComplaintCatalog
    triage_rules: TriageRules
    check_rules: CheckRules
    interaction_overrides: tuple[InteractionOverride, ...]
    guidelines: Guidelines
    adherence_rules: AdherenceRules
    adverse_event_rules: AdverseEventRules
    appointment_rules: AppointmentRules
    document_rules: DocumentRules
    lab_rules: LabRules
    referral_rules: ReferralRules
    drugs: DrugCatalog | None
    import_warnings: tuple[str, ...]


def load_knowledge(directory: Path, ohlp_csv: Path | None = None) -> Knowledge:
    """Load and validate every knowledge file under ``directory``.

    Layout: ``scales/*.yaml``, ``complaints.yaml``, ``rules/triage.yaml``, ``rules/checks.yaml``,
    ``rules/interaction_overrides.yaml``, ``kr/*.json``. Cross-checks: complaint codes used by triage
    rules exist; groups named in check rules exist in the drug vocabulary (when drugs are loaded).

    Raises:
        DefinitionError: any file is invalid (all problems listed in ``details``).
    """
    problems: list[Problem] = []

    def attempt[T](what: str, load: Callable[[], T]) -> T | None:
        try:
            return load()
        except DefinitionError as exc:
            problems.append(Problem(exc.code, exc.message, what))
            problems.extend(exc.details)
            return None

    rules_dir = directory / "rules"
    scales = attempt("scales", lambda: load_scales(directory / "scales"))
    complaints = attempt("complaints", lambda: load_complaints(directory / "complaints.yaml"))
    triage = attempt("triage", lambda: load_triage_rules(rules_dir / "triage.yaml"))
    checks = attempt("checks", lambda: load_check_rules(rules_dir / "checks.yaml"))
    overrides = attempt("overrides", lambda: load_interaction_overrides(rules_dir / "interaction_overrides.yaml"))
    adherence = attempt("adherence", lambda: load_adherence_rules(rules_dir / "adherence.yaml"))
    referrals = attempt("referrals", lambda: load_referral_rules(rules_dir / "referrals.yaml"))
    adverse = attempt("adverse_events", lambda: load_adverse_event_rules(rules_dir / "adverse_events.yaml"))
    appointments = attempt("appointments", lambda: load_appointment_rules(rules_dir / "appointments.yaml"))
    documents = attempt("documents", lambda: load_document_rules(rules_dir / "documents.yaml"))
    labs = attempt("labs", lambda: load_lab_rules(rules_dir / "labs.yaml"))
    guidelines_result = attempt("guidelines", lambda: load_guidelines(directory / "kr"))
    drugs_result = None
    if ohlp_csv is not None and checks is not None:
        patient_level = frozenset(rule.group for rule in checks.patient_level)
        vocabulary = frozenset(checks.interaction_groups)
        membership = tuple(
            (m.group, frozenset(normalize_name(s) for s in m.substances), m.atc_prefixes)
            for m in checks.group_membership
        )
        drugs_result = attempt(
            "drugs",
            lambda: load_catalog_from_csv(
                ohlp_csv,
                drug_groups=vocabulary,
                patient_level_groups=patient_level,
                membership=membership,
                substance_aliases=guidelines_result[0].vocabulary.substance_aliases if guidelines_result else None,
            ),
        )

    if complaints is not None and triage is not None:
        known = complaints.codes()
        for rule in triage.complaint_rules:
            for code in rule.complaints:
                if code not in known:
                    problems.append(Problem("unknown_complaint", f"triage.yaml: неизвестная жалоба {code}", code))
    if labs is not None and checks is not None:
        for lab_check in labs.checks:
            for group in lab_check.drugs.groups:
                if group not in checks.interaction_groups:
                    problems.append(Problem("unknown_group", f"labs.yaml: неизвестная группа {group}", group))
    if (
        scales is None
        or complaints is None
        or triage is None
        or checks is None
        or overrides is None
        or guidelines_result is None
        or adherence is None
        or referrals is None
        or adverse is None
        or appointments is None
        or documents is None
        or labs is None
        or problems
    ):
        raise DefinitionError("Ошибки в базе знаний", tuple(problems))

    guidelines, guideline_warnings = guidelines_result
    drugs, drug_warnings = drugs_result if drugs_result else (None, [])
    rules_digest = files_digest(
        [
            *sorted((directory / "scales").glob("*.yaml")),
            directory / "complaints.yaml",
            *sorted(rules_dir.glob("*.yaml")),
        ]
    )
    parts = [f"rules:{rules_digest}", guidelines.version, drugs.version if drugs else "ohlp:none"]
    version = "k:" + hashlib.sha256("|".join(parts).encode()).hexdigest()[:12]
    return Knowledge(
        version=version,
        scales=scales,
        complaints=complaints,
        triage_rules=triage,
        check_rules=checks,
        interaction_overrides=overrides,
        guidelines=guidelines,
        adherence_rules=adherence,
        adverse_event_rules=adverse,
        appointment_rules=appointments,
        document_rules=documents,
        lab_rules=labs,
        referral_rules=referrals,
        drugs=drugs,
        import_warnings=tuple(guideline_warnings) + tuple(drug_warnings),
    )
