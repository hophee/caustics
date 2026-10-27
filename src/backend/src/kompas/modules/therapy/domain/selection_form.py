"""Encounter selection controls. Source guideline rows stay unchanged (26 Oct agreement).

The form narrows a diagnosis by stage/history and indication flags. It never treats a
repeat encounter as resistance and never makes a patient diagnosis from a scale filter.
"""

from dataclasses import dataclass

from kompas.core.errors import InvalidInputError
from kompas.core.icd import IcdCode
from kompas.modules.knowledge.guidelines import ConditionKind, GuidelineRow


@dataclass(frozen=True, slots=True)
class SelectionAnswers:
    choice: str | None = None
    flags: tuple[str, ...] = ()


def form_kind(diagnosis: IcdCode) -> str:
    if diagnosis.category in {"F32", "F33"}:
        return "stage"
    if diagnosis.category == "F40" or diagnosis.code.startswith("F41.0"):
        return "history"
    return "line"


def initial_history(row: GuidelineRow) -> bool:
    return row.stage.startswith("1-я линия") or row.stage in {
        "По потребности",
        "Эпизодически",
        "Без указания линии",
    }


def stage_name(row: GuidelineRow) -> str:
    if row.stage.startswith("1 этап"):
        return "1 этап"
    return row.stage


def form_options(diagnosis: IcdCode, rows: tuple[GuidelineRow, ...]) -> dict[str, object]:
    kind = form_kind(diagnosis)
    if kind == "history":
        choices = [
            "Терапию ещё не получал",
            *dict.fromkeys(row.condition for row in rows if not initial_history(row) and row.condition),
        ]
        label, button, default = "Что уже было?", "Лечение получал. Специфицировать назначение", choices[0]
    elif kind == "stage":
        choices = list(dict.fromkeys(stage_name(row) for row in rows))
        label, button = "Этап терапии", "Пациент уже получал лечение"
        default = "1 этап" if "1 этап" in choices else (choices[0] if choices else "")
    else:
        choices = list(
            dict.fromkeys(row.stage for row in rows if row.stage.endswith("линия") or row.stage == "Резистентность")
        )
        label, button, default = "Линия терапии", "Уточнить линию", ""
    return {"kind": kind, "label": label, "button": button, "choices": choices, "default": default}


def stage_rows(
    diagnosis: IcdCode,
    rows: tuple[GuidelineRow, ...],
    choice: str | None,
) -> tuple[GuidelineRow, ...]:
    form = form_options(diagnosis, rows)
    value = form["default"] if choice is None else choice
    if value and value not in form["choices"]:
        raise InvalidInputError("Выбранный этап или условие не относится к этому диагнозу")
    kind = form["kind"]
    if kind == "stage":
        selected = tuple(row for row in rows if stage_name(row) == value)
    elif kind == "history":
        selected = (
            tuple(row for row in rows if initial_history(row))
            if value == form["default"]
            else tuple(row for row in rows if not initial_history(row) and row.condition == value)
        )
    elif not value:
        selected = rows
    else:
        selected = tuple(
            row
            for row in rows
            if row.stage == value
            or (value == "1-я линия" and row.stage in {"Без указания линии", "По потребности", "По показаниям"})
        )
    # Keep second-choice recommendations below the initial choice, preserving source order.
    return tuple(sorted(selected, key=lambda row: "второго выбора" in row.stage))


def indication_flags(diagnosis: IcdCode, rows: tuple[GuidelineRow, ...]) -> tuple[str, ...]:
    history = form_kind(diagnosis) == "history"
    return tuple(
        dict.fromkeys(
            row.condition
            for row in rows
            if row.condition
            and (
                (history and initial_history(row))
                or (not history and row.condition_kind in {ConditionKind.FLAG, ConditionKind.MIXED})
            )
        )
    )


def filter_rows(
    diagnosis: IcdCode,
    rows: tuple[GuidelineRow, ...],
    answers: SelectionAnswers,
) -> tuple[GuidelineRow, ...]:
    selected = stage_rows(diagnosis, rows, answers.choice)
    flags = set(indication_flags(diagnosis, selected))
    selected = tuple(row for row in selected if row.condition not in flags or row.condition in answers.flags)
    mixed = any(row.condition_kind is ConditionKind.MIXED and row.condition in answers.flags for row in selected)
    if mixed:
        # КР 301_3, 3.1.5: do not retain ordinary antidepressant monotherapy
        # next to the explicitly selected mixed-features branch.
        selected = tuple(
            row for row in selected if not (row.scheme == "Монотерапия" and row.window == "Антидепрессант")
        )
    return selected
