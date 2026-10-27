"""The agreed UI mapping, checked against the unchanged real guideline dataset."""

import pytest

from kompas.core.icd import IcdCode
from kompas.modules.therapy.domain.selection import diagnosis_rows
from kompas.modules.therapy.domain.selection_form import (
    SelectionAnswers,
    filter_rows,
    form_options,
    indication_flags,
    stage_rows,
)


def test_depression_first_stage_includes_second_choice(knowledge):
    code = IcdCode.parse("F32.1")
    rows = diagnosis_rows(code, knowledge.guidelines)
    selected = stage_rows(code, rows, None)
    assert selected
    assert all(r.stage.startswith("1 этап") for r in selected)
    assert any("второго выбора" in r.stage for r in selected)
    assert "второго выбора" in selected[-1].stage


@pytest.mark.parametrize("raw", ["F40.0", "F40.1", "F40.2", "F41.0"])
def test_history_is_verbatim_and_indications_remain_separate(knowledge, raw):
    code = IcdCode.parse(raw)
    rows = diagnosis_rows(code, knowledge.guidelines)
    form = form_options(code, rows)
    assert form["kind"] == "history"
    assert form["choices"][0] == "Терапию ещё не получал"
    for choice in form["choices"][1:]:
        selected = filter_rows(code, rows, SelectionAnswers(choice))
        assert selected and all(row.condition == choice for row in selected)
    initial = stage_rows(code, rows, None)
    flags = indication_flags(code, initial)
    assert all(row.condition not in flags for row in filter_rows(code, rows, SelectionAnswers()))
    if raw == "F40.1":
        assert any("Коитофобия" in flag for flag in flags)


@pytest.mark.parametrize("raw", ["F41.1", "F43.1"])
def test_anxiety_defaults_to_all_lines(knowledge, raw):
    code = IcdCode.parse(raw)
    rows = diagnosis_rows(code, knowledge.guidelines)
    assert stage_rows(code, rows, None) == tuple(sorted(rows, key=lambda r: "второго выбора" in r.stage))
    first = stage_rows(code, rows, "1-я линия")
    assert all(r.stage in {"1-я линия", "Без указания линии", "По потребности", "По показаниям"} for r in first)


def test_detailed_code_resolves_without_losing_original(knowledge):
    code = IcdCode.parse("F41.01")
    assert diagnosis_rows(code, knowledge.guidelines) == diagnosis_rows(IcdCode.parse("F41.0"), knowledge.guidelines)
    assert code.code == "F41.01"
