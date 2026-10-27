import pytest

from kompas.core.errors import InvalidInputError
from kompas.core.icd import IcdCode
from kompas.modules.cases.domain.encounter import validate_encounter
from kompas.modules.therapy.domain.selection import diagnosis_rows
from kompas.modules.therapy.domain.selection_form import SelectionAnswers, filter_rows


def test_optional_exam_and_tests_do_not_block_completion():
    content = dict(
        schema="encounter-v1", status="completed", diagnosis="F41.1", diagnosisSaved=True, risk="low", action="continue"
    )
    validate_encounter(content)
    with pytest.raises(InvalidInputError):
        validate_encounter({**content, "action": "dose"})
    with pytest.raises(InvalidInputError):
        validate_encounter({**content, "risk": "unknown"})
    validate_encounter({**content, "action": "dose", "prescriptionId": "validated-by-service"})


def test_mixed_features_exclude_ordinary_antidepressant_monotherapy(knowledge):
    diagnosis = IcdCode.parse("F32.1")
    rows = diagnosis_rows(diagnosis, knowledge.guidelines)
    mixed = next(row.condition for row in rows if row.condition.startswith("Смешанные черты, терапию"))
    selected = filter_rows(diagnosis, rows, SelectionAnswers(flags=(mixed,)))
    assert selected
    assert not any(row.scheme == "Монотерапия" and row.window == "Антидепрессант" for row in selected)
    assert any(row.condition == mixed for row in selected)


def test_supplied_questions_are_not_placeholders(knowledge):
    for code, count in [("HCL-32", 32), ("STAI", 40), ("HARS", 14)]:
        scale = knowledge.scales[code]
        assert sum(item.scored for item in scale.items) == count
        assert not any(item.text.startswith("Утверждение ") for item in scale.items)
        assert scale.source
