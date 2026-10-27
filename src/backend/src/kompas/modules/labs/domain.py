"""Demo lab value as the checks see it (pure; no DB). Always demo data (A13)."""

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class LabValue:
    code: str  # rules/labs.yaml tests[].code
    value: float
    measured_on: date


def latest_by_test(values: tuple[LabValue, ...]) -> dict[str, LabValue]:
    """The most recent value of each test (ties: the later one in the input)."""
    latest: dict[str, LabValue] = {}
    for v in values:
        current = latest.get(v.code)
        if current is None or v.measured_on >= current.measured_on:
            latest[v.code] = v
    return latest
