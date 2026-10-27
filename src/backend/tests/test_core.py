from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

from kompas.core.clock import FixedClock, local_date
from kompas.core.errors import DefinitionError, InvalidInputError
from kompas.core.icd import IcdCode, IcdPattern
from kompas.core.text import normalize_name


def test_normalize_name() -> None:
    assert normalize_name("  Сертралин®  Канон ") == "сертралин канон"
    assert normalize_name("Ёлка") == "елка"


@pytest.mark.parametrize(
    ("raw", "code"),
    [("f33.2", "F33.2"), (" F 33.2 ", "F33.2"), ("К72", "K72"), ("N18,3", "N18.3")],  # Cyrillic К
)
def test_icd_code_normalises(raw: str, code: str) -> None:
    parsed = IcdCode.parse(raw)
    assert parsed.code == code
    assert parsed.original == raw


def test_icd_code_rejects_garbage() -> None:
    with pytest.raises(InvalidInputError):
        IcdCode.parse("депрессия")


@pytest.mark.parametrize(
    ("pattern", "code", "expected"),
    [
        ("N18", "N18.3", True),
        ("N18", "N19", False),
        ("E74.3", "E74.31", True),
        ("E74.3", "E74.4", False),
        ("K70-K76", "K72.1", True),
        ("K70-K76", "K77", False),
    ],
)
def test_icd_pattern_matches(pattern: str, code: str, expected: bool) -> None:
    assert IcdPattern.parse(pattern).matches(IcdCode.parse(code)) is expected


def test_icd_pattern_rejects_bad_range() -> None:
    with pytest.raises(DefinitionError):
        IcdPattern.parse("K76-K70")


def test_local_date_uses_patient_zone() -> None:
    moment = datetime(2026, 10, 19, 22, 30, tzinfo=UTC)  # 01:30 next day in Moscow
    assert local_date(moment, ZoneInfo("Europe/Moscow")).day == 20
    assert local_date(moment, ZoneInfo("UTC")).day == 19


def test_fixed_clock_advances() -> None:
    clock = FixedClock(datetime(2026, 10, 19, tzinfo=UTC))
    clock.advance(timedelta(days=1))
    assert clock.now().day == 20
    with pytest.raises(ValueError):
        FixedClock(datetime(2026, 10, 19))
