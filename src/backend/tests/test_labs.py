"""Demo lab checks (A13): thresholds from rules/labs.yaml × the drugs in the basket."""

import dataclasses
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from kompas.modules.cases.domain.profile import Sex
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.drugs import DrugCatalog
from kompas.modules.knowledge.lab_rules import LabCheck, LabRules
from kompas.modules.labs.domain import LabValue, latest_by_test
from kompas.modules.therapy.domain.basket import Draft
from kompas.modules.therapy.domain.checks import CheckContext, CheckReport, Severity, Warning, check_basket
from tests.conftest import TODAY, make_ctx, make_patient
from tests.test_therapy import item


def run(knowledge: Knowledge, catalog: DrugCatalog, mnn: str, *labs: LabValue, **patient: object) -> CheckReport:
    ctx: CheckContext = dataclasses.replace(
        make_ctx(knowledge, make_patient(**patient)), labs=labs, lab_rules=knowledge.lab_rules
    )
    return check_basket((), Draft((item(catalog, "a", mnn),)), ctx)


def lab(report: CheckReport, code: str) -> Warning | None:
    return next((w for w in report.warnings if w.code == code), None)


def value(code: str, v: float, days_ago: int = 3) -> LabValue:
    return LabValue(code, v, TODAY - timedelta(days=days_ago))


def test_qtc_threshold_depends_on_sex(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    # амитриптилин is in «удлиняющие QT»; limits 450 (male) / 470 (female)
    assert lab(run(knowledge, catalog, "амитриптилин", value("qtc", 460)), "lab:qtc_prolonged") is None
    female = lab(run(knowledge, catalog, "амитриптилин", value("qtc", 480)), "lab:qtc_prolonged")
    assert female is not None and female.severity is Severity.CONTRAINDICATED
    assert "демо" in female.message and female.sources[0].document == "демо-анализ"
    male = lab(run(knowledge, catalog, "амитриптилин", value("qtc", 460), sex=Sex.MALE), "lab:qtc_prolonged")
    assert male is not None and male.severity is Severity.CONTRAINDICATED
    # a drug outside the group is not affected
    assert lab(run(knowledge, catalog, "сертралин", value("qtc", 520)), "lab:qtc_prolonged") is None


def test_missing_or_stale_qtc_is_grey_never_normal(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    missing = lab(run(knowledge, catalog, "кветиапин"), "lab_missing:qtc_prolonged")
    assert missing is not None and missing.severity is Severity.NO_DATA
    stale = run(knowledge, catalog, "кветиапин", value("qtc", 520, days_ago=120))
    assert lab(stale, "lab:qtc_prolonged") is None  # a stale value is not used
    grey = lab(stale, "lab_missing:qtc_prolonged")
    assert grey is not None and "последнее" in grey.message
    # rules with when_missing: none stay silent without a value
    assert lab(run(knowledge, catalog, "лития карбонат"), "lab_missing:lithium_high") is None


def test_egfr_follows_the_label_renal_status(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    low = value("egfr", 25)
    lithium = lab(run(knowledge, catalog, "лития карбонат", low), "lab:egfr_low")  # renal: contraindicated
    assert lithium is not None and lithium.severity is Severity.CONTRAINDICATED
    assert any(s.document == "ОХЛП" for s in lithium.sources)
    escitalopram = lab(run(knowledge, catalog, "эсциталопрам", low), "lab:egfr_low")  # dose_adjustment
    assert escitalopram is not None and escitalopram.severity is Severity.CAUTION
    assert lab(run(knowledge, catalog, "сертралин", low), "lab:egfr_low") is None  # no_adjustment
    assert lab(run(knowledge, catalog, "лития карбонат", value("egfr", 60)), "lab:egfr_low") is None


def test_liver_enzymes_sodium_and_lithium(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    alt = lab(run(knowledge, catalog, "сертралин", value("alt", 200)), "lab:alt_high")  # hepatic: contraindicated
    assert alt is not None and alt.severity is Severity.CONTRAINDICATED
    assert lab(run(knowledge, catalog, "сертралин", value("alt", 80)), "lab:alt_high") is None

    elderly = {"birth_date": date(1955, 1, 1)}
    sodium = lab(run(knowledge, catalog, "сертралин", value("sodium", 130), **elderly), "lab:sodium_low")
    assert sodium is not None and sodium.severity is Severity.CAUTION
    assert lab(run(knowledge, catalog, "сертралин", value("sodium", 130)), "lab:sodium_low") is None  # 34 years

    level = lab(run(knowledge, catalog, "лития карбонат", value("lithium", 1.5)), "lab:lithium_high")
    assert level is not None and level.severity is Severity.CONTRAINDICATED
    assert lab(run(knowledge, catalog, "лития карбонат", value("lithium", 0.8)), "lab:lithium_high") is None


def test_latest_value_wins_and_no_lab_rules_means_no_lab_checks(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    older, newer = value("qtc", 500, days_ago=20), value("qtc", 440, days_ago=2)
    assert latest_by_test((newer, older))["qtc"] == newer
    assert lab(run(knowledge, catalog, "амитриптилин", older, newer), "lab:qtc_prolonged") is None
    plain = check_basket((), Draft((item(catalog, "a", "амитриптилин"),)), make_ctx(knowledge))
    assert not [w for w in plain.warnings if w.code.startswith("lab")]


def test_lab_rules_validation() -> None:
    with pytest.raises(ValidationError):
        LabCheck.model_validate(
            {
                "code": "x",
                "test": "qtc",
                "above": {"male": 450},
                "drugs": {"groups": ["g"]},
                "outcome": "caution",
                "message": "m",
            }
        )  # a per-sex threshold needs both sexes
    with pytest.raises(ValidationError):
        LabCheck.model_validate(
            {
                "code": "x",
                "test": "qtc",
                "above": {"any": 1},
                "drugs": {"groups": ["g"]},
                "outcome": "from_label",
                "message": "m",
            }
        )  # from_label needs a label field
    with pytest.raises(ValidationError):
        LabRules.model_validate(
            {
                "tests": [],
                "checks": [
                    {
                        "code": "x",
                        "test": "nope",
                        "below": {"any": 1},
                        "drugs": {"label_field": "renal"},
                        "outcome": "caution",
                        "message": "m",
                    }
                ],
            }
        )  # unknown test
