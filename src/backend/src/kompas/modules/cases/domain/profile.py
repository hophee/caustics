"""The patient's safety profile: everything drug checks need to know about the patient.

Unknown is not the same as "no" (spec §4):
* yes/no facts are ``Tristate``;
* list facts are ``tuple | None`` where ``None`` = never asked and ``()`` = asked, none.
List facts deliberately have **no defaults**: every caller must say which of the two it means.
"""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from zoneinfo import ZoneInfo

from kompas.core.icd import IcdCode, IcdPattern


class Tristate(StrEnum):
    YES = "yes"
    NO = "no"
    UNKNOWN = "unknown"


class Sex(StrEnum):
    FEMALE = "female"
    MALE = "male"


class SuicideRisk(StrEnum):
    """Doctor's assessment (spec §6). Selection reads it from here, not from separate answers."""

    LOW = "low"
    HIGH = "high"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class PatientProfile:
    """Safety-relevant facts recorded by the doctor.

    ``diagnoses``: all current conditions (main and comorbid).
    ``allergies``, ``past_intolerance``, ``past_inefficacy``: normalised МНН.
    ``intolerant_excipients``: same vocabulary as ``DrugLabel.excipients`` (лактоза, ...).
    For ``sex=MALE``, pregnancy and lactation are not applicable: ``is_pregnant`` /
    ``is_lactating`` return NO regardless of the stored value.
    """

    birth_date: date
    sex: Sex
    timezone: ZoneInfo
    pregnant: Tristate
    lactating: Tristate
    substance_use: Tristate
    suicide_risk: SuicideRisk
    diagnoses: tuple[IcdCode, ...]
    allergies: tuple[str, ...] | None
    intolerant_excipients: tuple[str, ...] | None
    past_intolerance: tuple[str, ...] | None
    past_inefficacy: tuple[str, ...] | None
    weight_kg: float | None = None

    def age_on(self, day: date) -> int:
        """Full years on ``day``."""
        had_birthday = (day.month, day.day) >= (self.birth_date.month, self.birth_date.day)
        return day.year - self.birth_date.year - (0 if had_birthday else 1)

    def is_pregnant(self) -> Tristate:
        """Pregnancy, with MALE → NO."""
        return Tristate.NO if self.sex is Sex.MALE else self.pregnant

    def is_lactating(self) -> Tristate:
        """Lactation, with MALE → NO."""
        return Tristate.NO if self.sex is Sex.MALE else self.lactating

    def has_condition(self, pattern: IcdPattern) -> bool:
        """True if any diagnosis matches ``pattern``."""
        return any(pattern.matches(code) for code in self.diagnoses)

    def matching_diagnoses(self, pattern: IcdPattern) -> tuple[IcdCode, ...]:
        """Diagnoses that match ``pattern`` (for messages: which code triggered a warning)."""
        return tuple(code for code in self.diagnoses if pattern.matches(code))
