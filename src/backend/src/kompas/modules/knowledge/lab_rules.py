"""Demo lab tests and the checks that use them: ``knowledge_data/rules/labs.yaml`` (A13).

There is no real lab module; values are demo data entered by the doctor. Which tests exist, their
plausible input ranges, the thresholds and which drugs they concern are all data awaiting clinical
review — the team edits the file, not the code (D-013).
"""

from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kompas.modules.knowledge._files import read_yaml, validate_model


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class LabTest(_Model):
    code: str
    label: str
    unit: str
    min: float  # plausible input range (typos refused), not a reference range
    max: float

    @model_validator(mode="after")
    def _range(self) -> "LabTest":
        if self.min >= self.max:
            raise ValueError(f"{self.code}: min must be below max")
        return self


class Threshold(_Model):
    """A limit for everyone (``any``) or per sex (``male`` / ``female``)."""

    any: float | None = None
    male: float | None = None
    female: float | None = None

    @model_validator(mode="after")
    def _complete(self) -> "Threshold":
        if self.any is None and (self.male is None or self.female is None):
            raise ValueError("threshold needs 'any' or both 'male' and 'female'")
        return self

    def for_sex(self, sex: str) -> float:
        value = {"male": self.male, "female": self.female}.get(sex)
        value = value if value is not None else self.any
        assert value is not None  # guaranteed by _complete for both sexes
        return value


class DrugSelector(_Model):
    """Which drugs a lab check concerns: members of interaction ``groups`` or drugs whose label has a
    non-neutral status for ``label_field`` (renal / hepatic)."""

    groups: tuple[str, ...] = ()
    label_field: Literal["renal", "hepatic"] | None = None

    @model_validator(mode="after")
    def _one(self) -> "DrugSelector":
        if bool(self.groups) == (self.label_field is not None):
            raise ValueError("drugs: give either 'groups' or 'label_field'")
        return self


class LabCheck(_Model):
    code: str
    test: str
    above: Threshold | None = None
    below: Threshold | None = None
    min_age: int | None = Field(None, ge=0)
    drugs: DrugSelector
    outcome: Literal["caution", "contraindicated", "from_label"]
    when_missing: Literal["none", "no_data"] = "none"
    message: str

    @model_validator(mode="after")
    def _direction(self) -> "LabCheck":
        if (self.above is None) == (self.below is None):
            raise ValueError(f"{self.code}: give exactly one of 'above' / 'below'")
        if self.outcome == "from_label" and self.drugs.label_field is None:
            raise ValueError(f"{self.code}: outcome from_label needs drugs.label_field")
        return self

    def crossed(self, value: float, sex: str) -> bool:
        if self.above is not None:
            return value > self.above.for_sex(sex)
        assert self.below is not None
        return value < self.below.for_sex(sex)

    def threshold_text(self, sex: str) -> str:
        if self.above is not None:
            return f"> {self.above.for_sex(sex):g}"
        assert self.below is not None
        return f"< {self.below.for_sex(sex):g}"


class LabRules(_Model):
    stale_after_days: int = Field(90, ge=1)
    tests: tuple[LabTest, ...]
    checks: tuple[LabCheck, ...] = ()

    @model_validator(mode="after")
    def _refs(self) -> "LabRules":
        codes = [t.code for t in self.tests]
        if len(set(codes)) != len(codes):
            raise ValueError("lab test codes must be unique")
        for check in self.checks:
            if check.test not in codes:
                raise ValueError(f"{check.code}: unknown test {check.test}")
        if len({c.code for c in self.checks}) != len(self.checks):
            raise ValueError("lab check codes must be unique")
        return self

    def test(self, code: str) -> LabTest | None:
        return next((t for t in self.tests if t.code == code), None)


def load_lab_rules(path: Path) -> LabRules:
    """Parse ``labs.yaml``. Raises DefinitionError for a malformed file."""
    return validate_model(LabRules, read_yaml(path), path.name)
