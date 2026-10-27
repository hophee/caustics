"""Shared fixtures: real knowledge files plus a small real-data drug sample (tests/fixtures)."""

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from kompas.core.icd import IcdCode
from kompas.modules.cases.domain.profile import PatientProfile, Sex, SuicideRisk, Tristate
from kompas.modules.knowledge.bundle import Knowledge, load_knowledge
from kompas.modules.knowledge.drugs import DrugCatalog
from kompas.modules.therapy.domain.checks import CheckContext

BACKEND = Path(__file__).resolve().parents[1]
KNOWLEDGE_DIR = BACKEND / "knowledge_data"
OHLP_SAMPLE = BACKEND / "tests" / "fixtures" / "ohlp_sample.csv"
TODAY = date(2026, 10, 19)


@pytest.fixture(scope="session")
def knowledge() -> Knowledge:
    return load_knowledge(KNOWLEDGE_DIR, OHLP_SAMPLE)


@pytest.fixture(scope="session")
def catalog(knowledge: Knowledge) -> DrugCatalog:
    assert knowledge.drugs is not None
    return knowledge.drugs


def make_patient(**overrides: object) -> PatientProfile:
    """Анна-like patient; every list fact explicit (no defaults by design)."""
    fields: dict[str, object] = {
        "birth_date": date(1992, 3, 1),
        "sex": Sex.FEMALE,
        "timezone": ZoneInfo("Europe/Moscow"),
        "pregnant": Tristate.NO,
        "lactating": Tristate.NO,
        "substance_use": Tristate.NO,
        "suicide_risk": SuicideRisk.LOW,
        "diagnoses": (IcdCode.parse("F33.2"),),
        "allergies": (),
        "intolerant_excipients": (),
        "past_intolerance": (),
        "past_inefficacy": (),
    }
    fields.update(overrides)
    return PatientProfile(**fields)  # type: ignore[arg-type]


def make_ctx(knowledge: Knowledge, patient: PatientProfile | None = None, today: date = TODAY) -> CheckContext:
    assert knowledge.drugs is not None
    return CheckContext(
        knowledge.version,
        patient or make_patient(),
        knowledge.drugs,
        knowledge.check_rules,
        knowledge.interaction_overrides,
        today,
    )
