from datetime import date
from typing import Any

import pytest

from kompas.core.errors import DefinitionError, InvalidInputError
from kompas.modules.assessments.domain.dynamics import (
    Measurement,
    ResponseCriteria,
    ResponseStatus,
    assess_response,
    measurements_of,
)
from kompas.modules.assessments.domain.scoring import score
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.scales import Direction, parse_scale
from kompas.modules.knowledge.triage_rules import UrgentScenario
from tests.scale_answers import complete_answers


def answers(*values: int) -> dict[int, int]:
    return {i + 1: v for i, v in enumerate(values)}


def test_all_scale_files_load(knowledge: Knowledge) -> None:
    assert len(knowledge.scales) == 44
    assert {"PHQ-9", "GAD-7", "HDRS-17", "C-SSRS", "STAI", "HCL-32", "FFiCD", "SIFS", "ITT"} <= set(knowledge.scales)


def test_phq9_band_and_critical(knowledge: Knowledge) -> None:
    d = knowledge.scales["PHQ-9"]
    result = score(d, answers(2, 2, 3, 2, 2, 2, 2, 3, 1))
    assert result.total == 19
    assert result.band is not None and result.band.code == "mod_severe"
    assert result.band.label == "Умеренно тяжёлая"
    assert [h.item for h in result.critical_hits] == [9]


def test_critical_rule_may_set_its_own_urgent_scenario(knowledge: Knowledge) -> None:
    d = knowledge.scales["PHQ-9"]
    assert [h.scenario for h in score(d, answers(0, 0, 0, 0, 0, 0, 0, 0, 1)).critical_hits] == [None]  # → default
    rule = d.critical[0].model_copy(update={"scenario": UrgentScenario.CHECK_SOON})
    own = d.model_copy(update={"critical": (rule,)})
    assert [h.scenario for h in score(own, answers(0, 0, 0, 0, 0, 0, 0, 0, 1)).critical_hits] == [
        UrgentScenario.CHECK_SOON
    ]


def test_phq9_no_critical_when_item9_zero(knowledge: Knowledge) -> None:
    result = score(knowledge.scales["PHQ-9"], answers(0, 0, 0, 0, 0, 0, 0, 0, 0))
    assert not result.is_critical
    assert result.band is not None and result.band.code == "minimal"


def test_hdrs17_is_a_clinician_scale_with_bands(knowledge: Knowledge) -> None:
    d = knowledge.scales["HDRS-17"]
    assert d.filled_by == "clinician"
    result = score(d, answers(3, 2, 1, 2, 1, 2, 2, 1, 1, 2, 1, 1, 0, 0, 0, 0, 0))
    assert result.total == 19
    assert result.band is not None and result.band.code == "severe"


def test_hdrs17_score_23_has_a_band(knowledge: Knowledge) -> None:
    # КР key leaves 23 uncovered; our file assigns it to "крайне тяжёлое" (provisional).
    result = score(knowledge.scales["HDRS-17"], answers(4, 4, 4, 2, 2, 2, 4, 1, 0, 0, 0, 0, 0, 0, 0, 0, 0))
    assert result.total == 23
    assert result.band is not None and result.band.code == "extreme"


def test_cssrs_rule_is_doctor_only_and_critical(knowledge: Knowledge) -> None:
    d = knowledge.scales["C-SSRS"]
    serious = score(d, complete_answers(d, {2: 1, 3: 1}))
    assert serious.band is not None and serious.band.code == "serious"
    assert "serious" in [h.band_code for h in serious.critical_hits]
    assert 3 in [h.item for h in serious.critical_hits]
    assert score(d, complete_answers(d, {"attempt": 1})).band.code == "extreme"  # type: ignore[union-attr]
    assert not score(d, complete_answers(d)).is_critical


def test_stai_subscales_with_reverse_items(knowledge: Knowledge) -> None:
    d = knowledge.scales["STAI"]
    result = score(d, {i: 2 for i in range(1, 41)})
    values = {s.code: s.value for s in result.subscores}
    assert values == {"reactive": 50, "personal": 47}  # 20-20+50, 26-14+35
    assert result.total is None and result.band is None
    assert [s.band.code for s in result.subscores] == ["high", "high"]


def test_hcl32_count_threshold(knowledge: Knowledge) -> None:
    d = knowledge.scales["HCL-32"]
    assert score(d, complete_answers(d, {i: 1 for i in range(1, 15)})).band.code == "positive"  # type: ignore[union-attr]
    assert score(d, complete_answers(d, {i: 1 for i in range(1, 14)})).band.code == "negative"  # type: ignore[union-attr]


def test_answers_validated_with_pointers(knowledge: Knowledge) -> None:
    with pytest.raises(InvalidInputError) as exc:
        score(knowledge.scales["PHQ-9"], {1: 5, 2: 0, 99: 1})
    pointers = {p.pointer for p in exc.value.details}
    assert {"answers.1", "answers.3", "answers.99"} <= pointers


def _scale(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        "code": "T",
        "version": 1,
        "name": "test",
        "measures": "depression",
        "direction": "higher_is_worse",
        "filled_by": "patient",
        "source": "test",
        "answer_sets": {"a": [{"value": 0, "label": "0"}, {"value": 1, "label": "1"}]},
        "items": [{"id": 1, "text": "q1", "answers": "a"}, {"id": 2, "text": "q2", "answers": "a"}],
        "scoring": {
            "method": "sum",
            "bands": [
                {"lower": 0, "upper": 0, "code": "lo", "level": 0, "label": "lo"},
                {"lower": 1, "upper": 2, "code": "hi", "level": 2, "label": "hi"},
            ],
        },
    }
    data.update(overrides)
    return data


def test_valid_test_scale_parses() -> None:
    assert parse_scale(_scale()).code == "T"


@pytest.mark.parametrize(
    ("bands", "fragment"),
    [
        ([(0, 0), (2, 2)], "пропуск"),
        ([(0, 1), (1, 2)], "пересекаются"),
        ([(0, 0), (1, 1)], "заканчивается"),
    ],
)
def test_band_coverage_is_validated(bands: list[tuple[int, int]], fragment: str) -> None:
    scoring = {
        "method": "sum",
        "bands": [
            {"lower": lo, "upper": hi, "code": f"b{i}", "level": 0, "label": "x"} for i, (lo, hi) in enumerate(bands)
        ],
    }
    with pytest.raises(DefinitionError) as exc:
        parse_scale(_scale(scoring=scoring))
    assert any(fragment in p.message for p in exc.value.details)


def test_definitions_carry_nothing_for_the_patient() -> None:
    # D-109: the patient never sees a result, so a definition with patient texts or a publication
    # mode is out of date and must fail loudly instead of being half-used.
    scoring = _scale()["scoring"]
    scoring["bands"][0]["patient_text"] = "lo"
    with pytest.raises(DefinitionError):
        parse_scale(_scale(scoring=scoring))
    with pytest.raises(DefinitionError):
        parse_scale(_scale(publication="immediate"))


def test_clinician_scale_parses() -> None:
    assert parse_scale(_scale(filled_by="clinician")).filled_by == "clinician"


def test_unknown_critical_item_rejected() -> None:
    with pytest.raises(DefinitionError):
        parse_scale(_scale(critical=[{"item": 7, "min_value": 1}]))


CRITERIA = ResponseCriteria(response_reduction=0.5, remission_at_most=7, evaluate_after_days=21)


def test_response_statuses() -> None:
    start = date(2026, 9, 21)
    base = Measurement(start, 24)
    later = date(2026, 10, 19)
    assert (
        assess_response([base], CRITERIA, Direction.HIGHER_IS_WORSE, start).status is ResponseStatus.INSUFFICIENT_DATA
    )
    early = [base, Measurement(date(2026, 9, 28), 20)]
    assert assess_response(early, CRITERIA, Direction.HIGHER_IS_WORSE, start).status is ResponseStatus.TOO_EARLY
    no = assess_response([base, Measurement(later, 19)], CRITERIA, Direction.HIGHER_IS_WORSE, start)
    assert no.status is ResponseStatus.NO_RESPONSE and round(no.improvement or 0, 2) == 0.21
    yes = assess_response([base, Measurement(later, 11)], CRITERIA, Direction.HIGHER_IS_WORSE, start)
    assert yes.status is ResponseStatus.RESPONSE
    rem = assess_response([Measurement(later, 6), base], CRITERIA, Direction.HIGHER_IS_WORSE, start)
    assert rem.status is ResponseStatus.REMISSION  # order of input doesn't matter
    zero = assess_response([Measurement(start, 0), Measurement(later, 3)], CRITERIA, Direction.HIGHER_IS_WORSE, start)
    assert zero.status is ResponseStatus.INSUFFICIENT_DATA


def test_measurements_split_subscales(knowledge: Knowledge) -> None:
    result = score(knowledge.scales["STAI"], {i: 2 for i in range(1, 41)})
    keys = [k for k, _ in measurements_of(result, date(2026, 10, 19))]
    assert keys == [("STAI", "reactive"), ("STAI", "personal")]
