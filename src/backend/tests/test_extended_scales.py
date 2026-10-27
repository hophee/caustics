import math
import random
from pathlib import Path

import pytest

from kompas.core.errors import InvalidInputError
from kompas.modules.assessments.domain.scoring import item_critical_hits, score
from kompas.modules.knowledge.scales import load_scale, visible_items
from tests.scale_answers import complete_answers

SCALES = Path(__file__).resolve().parents[1] / "knowledge_data" / "scales"


@pytest.mark.parametrize("path", sorted(SCALES.glob("*.yaml")), ids=lambda p: p.stem)
def test_every_scale_scores_valid_forms_at_boundaries_and_between(path: Path) -> None:
    definition = load_scale(path)
    rng = random.Random(42)
    samples = [complete_answers(definition), complete_answers(definition, maximum=True)]
    for _ in range(12):
        answers: dict[int | str, int] = {}
        for item in definition.items:
            if item not in visible_items(definition, answers):
                continue
            answers[item.id] = (
                rng.randint(item.min, item.max or item.min + 5) if item.input else rng.choice(item.options).value
            )
        samples.append(answers)
    for answers in samples:
        result = score(definition, answers)
        assert result.total is None or math.isfinite(result.total)
        assert all(math.isfinite(s.value) for s in result.subscores)
        assert len({s.code for s in result.subscores}) == len(result.subscores)


def test_mississippi_reverse_items() -> None:
    d = load_scale(SCALES / "mississippi_military.yaml")
    assert score(d, complete_answers(d)).total == 75  # 25 direct * 1 + 10 reversed * 5
    assert score(d, complete_answers(d, maximum=True)).total == 135


def test_sifs_means_reverse_items_and_severity() -> None:
    d = load_scale(SCALES / "sifs.yaml")
    result = score(d, {i: 2 for i in range(1, 25)})
    assert result.total == 2
    assert result.band.code == "moderate"
    assert {s.code: s.value for s in result.subscores}["total"] == 8


def test_scl90_indices_and_zero_denominator() -> None:
    d = load_scale(SCALES / "scl90r.yaml")
    zero = score(d, complete_answers(d))
    assert {s.code: s.value for s in zero.subscores}["PDSI"] == 0
    result = score(d, complete_answers(d, {1: 4, 2: 2}))
    values = {s.code: s.value for s in result.subscores}
    assert values["GSI"] == pytest.approx(6 / 90)
    assert values["PSI"] == 2
    assert values["PDSI"] == 3


def test_itt_weighted_scores_and_population_norms() -> None:
    d = load_scale(SCALES / "itt.yaml")
    result = score(d, complete_answers(d, {"Л1": 3, "Л2": 2, "Л4": 1}))
    sub = next(s for s in result.subscores if s.code == "ED_trait")
    assert sub.value == 74 + 49 + 27
    assert sub.norms == {"adults_and_young_men": 7, "young_women": 6}


def test_scid_count_conditions() -> None:
    d = load_scale(SCALES / "scid_ptsd.yaml")
    scenario = {"I1": 1, "I2": 1, "I3a": 1, "I3b": 1, "I3c": 1, "I4a": 1, "I4b": 1, "I5": 1}
    assert score(d, complete_answers(d, scenario)).band.code == "ptsd_current"
    scenario["I3c"] = 0
    assert score(d, complete_answers(d, scenario)).band.code == "no_ptsd"


def test_psp_numeric_limits_and_single_score() -> None:
    d = load_scale(SCALES / "psp.yaml")
    assert score(d, complete_answers(d, {"score": 85})).total == 85
    for value in (0, 101, 1.5, True):
        with pytest.raises(InvalidInputError):
            score(d, complete_answers(d, {"score": value}))


def test_cssrs_hidden_questions_and_partial_critical_answers() -> None:
    d = load_scale(SCALES / "cssrs.yaml")
    answers = complete_answers(d)
    assert 3 not in answers and "potential_lethality" not in answers
    assert score(d, answers).band.code == "below"
    attempt = complete_answers(d, {"attempt": 1})
    assert {"attempt_count", "actual_lethality", "potential_lethality"} <= attempt.keys()
    assert "potential_lethality" not in complete_answers(d, {"attempt": 1, "actual_lethality": 1})
    for item in ("attempt", "interrupted", "aborted", "preparatory", "behavior"):
        assert score(d, complete_answers(d, {item: 1})).band.code == "extreme"
    assert item_critical_hits(d, {"attempt": 1})
    assert item_critical_hits(d, {3: 1})  # Safety signal survives an inconsistent/partial interview.
    for item in (4, 5, "interrupted", "aborted", "behavior"):
        assert item_critical_hits(d, {item: 1})
    answers[2] = 1
    with pytest.raises(InvalidInputError):
        score(d, answers)  # newly visible questions must be answered


def test_bfcrs_screening_count_and_raw_score_without_invented_band() -> None:
    d = load_scale(SCALES / "bfcrs.yaml")
    assert not item_critical_hits(d, {1: 1})
    assert item_critical_hits(d, {1: 1, 2: 1})
    result = score(d, complete_answers(d, {1: 1, 2: 1}))
    assert result.total == 2 and result.band is None


def test_hcl_extra_answers_are_not_scored() -> None:
    d = load_scale(SCALES / "hcl32.yaml")
    result = score(d, complete_answers(d, {"q1": 6, "q7": 1, "q8": 366}))
    assert result.total == 0 and result.band.code == "negative"


def test_pdss_keeps_diagnostic_variants_without_choosing_for_the_doctor() -> None:
    d = load_scale(SCALES / "pdss_sr.yaml")
    result = score(d, complete_answers(d, {1: 4, 2: 4, 3: 4, 4: 4}))
    assert result.total == 16 and result.band is None
    assert any("Без агорафобии: Очень тяжёлая" in line for line in result.explanation)
    assert any("С агорафобией: Тяжёлая" in line for line in result.explanation)


def test_incomplete_source_does_not_produce_a_low_risk_category() -> None:
    d = load_scale(SCALES / "sad_persons.yaml")
    assert score(d, complete_answers(d)).band is None
    assert score(d, complete_answers(d, maximum=True)).is_critical
