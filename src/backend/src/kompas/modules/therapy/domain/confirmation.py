"""Confirming a draft into a new prescription version (D-046, D-047, D-049).

``confirm`` runs the checks itself, so it can't be fed a stale or client-made warning list. Red
warnings that need a decision (see ``CheckReport.requiring_justification``) must have a written
justification, given on the second confirmation. The result is the full resulting regimen as a new
version: continued + new items, and the stopped refs.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime

from kompas.core.errors import InvalidInputError, Problem
from kompas.modules.therapy.domain.basket import CurrentTherapyItem, Draft, TherapyItem, resulting_therapy
from kompas.modules.therapy.domain.checks import CheckContext, CheckReport, Severity, Warning, check_basket
from kompas.modules.therapy.domain.selection import Provenance


@dataclass(frozen=True, slots=True)
class ConfirmedItem:
    """One item of the confirmed regimen, with the label suggestion shown next to the doctor's dose
    (A04: the final dose is recorded separately from the suggestion) and the server-derived
    provenance (D-042; None for continued current therapy)."""

    item: TherapyItem
    suggested_dose_text: str | None
    provenance: Provenance | None


@dataclass(frozen=True, slots=True)
class ConfirmedPrescription:
    """Immutable snapshot stored on confirmation: what is prescribed and what the doctor was shown.

    ``justifications``: (warning id, text) pairs written for this confirmation — only for red
    warnings that required one. ``justified_warning_ids`` = those ids plus previously justified red
    warnings that are still red: pass it as ``previously_justified`` to the next version. A yellow
    warning is never recorded as justified, so if it later turns red it needs a new justification.
    """

    items: tuple[ConfirmedItem, ...]
    stopped_refs: frozenset[str]
    warnings_at_confirmation: tuple[Warning, ...]
    justifications: tuple[tuple[str, str], ...]
    justified_warning_ids: frozenset[str]
    confirmed_by: str
    confirmed_at: datetime
    knowledge_version: str


def confirmation_problems(
    current: tuple[CurrentTherapyItem, ...],
    draft: Draft,
    ctx: CheckContext,
    justifications: Mapping[str, str],
    previously_justified: frozenset[str],
    provenances: Mapping[str, Provenance],
) -> tuple[Problem, ...]:
    """All reasons the draft can't be confirmed yet (empty = can confirm).

    * the draft changes something (a new item or a stop);
    * the draft is structurally valid (regimens valid, D-047: positive duration);
    * every new item has a server-computed provenance in ``provenances`` (keyed by item key;
      the service computes them with ``selection.provenance``);
    * every red warning from ``requiring_justification(previously_justified)`` has a justification of
      at least ``ctx.rules.justification_min_length`` characters after stripping
      (problem code ``justification_required``, pointer = warning id).
    Justifications for warnings that no longer exist are ignored.
    """
    structural = draft.problems(current)
    if structural:
        return tuple(structural)
    return _problems(draft, check_basket(current, draft, ctx), ctx, justifications, previously_justified, provenances)


def _problems(
    draft: Draft,
    report: CheckReport,
    ctx: CheckContext,
    justifications: Mapping[str, str],
    previously_justified: frozenset[str],
    provenances: Mapping[str, Provenance],
) -> tuple[Problem, ...]:
    problems: list[Problem] = []
    if not draft.new_items and not draft.stopped:
        problems.append(Problem("nothing_to_confirm", "В назначении нет изменений"))
    for item in draft.new_items:
        if item.key not in provenances:
            problems.append(Problem("provenance_missing", f"Нет данных о происхождении препарата {item.key}", item.key))
    minimum = ctx.rules.justification_min_length
    for warning in report.requiring_justification(previously_justified):
        text = (justifications.get(warning.id) or "").strip()
        if len(text) < minimum:
            problems.append(
                Problem(
                    "justification_required",
                    f"Нужно обоснование (не короче {minimum} символов): {warning.message}",
                    warning.id,
                )
            )
    return tuple(problems)


def confirm(
    current: tuple[CurrentTherapyItem, ...],
    draft: Draft,
    ctx: CheckContext,
    justifications: Mapping[str, str],
    previously_justified: frozenset[str],
    provenances: Mapping[str, Provenance],
    *,
    doctor_id: str,
    now: datetime,
) -> ConfirmedPrescription:
    """Re-run the checks and build the snapshot (knowledge version taken from ``ctx``).

    Raises:
        InvalidInputError: ``confirmation_problems`` is not empty (problems in ``details``).
    """
    structural = draft.problems(current)
    if structural:
        raise InvalidInputError("Черновик назначения некорректен", tuple(structural))
    report = check_basket(current, draft, ctx)
    problems = _problems(draft, report, ctx, justifications, previously_justified, provenances)
    if problems:
        raise InvalidInputError("Назначение нельзя подтвердить", problems)
    required = report.requiring_justification(previously_justified)
    carried = frozenset(
        w.id for w in report.warnings if w.severity is Severity.CONTRAINDICATED and w.id in previously_justified
    )
    items = []
    for item in resulting_therapy(current, draft):
        suggested = None
        if item.is_new and item.label_id:
            dose = ctx.drugs.get(item.label_id).dose
            suggested = dose.start_text.quote or dose.start_text.value
        items.append(ConfirmedItem(item, suggested, provenances.get(item.key) if item.is_new else None))
    return ConfirmedPrescription(
        items=tuple(items),
        stopped_refs=draft.stopped,
        warnings_at_confirmation=report.warnings,
        justifications=tuple(sorted((w.id, justifications[w.id].strip()) for w in required)),
        justified_warning_ids=frozenset(w.id for w in required) | carried,
        confirmed_by=doctor_id,
        confirmed_at=now,
        knowledge_version=report.knowledge_version,
    )
