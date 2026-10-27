"""Diary triage policy: ``knowledge_data/rules/triage.yaml`` (D-032, D-033, D-035).

All thresholds are team proposals awaiting clinical review.
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from kompas.modules.knowledge._files import read_yaml, validate_model

_RANK = {"stable": 0, "attention": 1, "urgent": 2}
_LABEL = {"stable": "Стабильно", "attention": "Требует внимания", "urgent": "Срочно"}


class TriageLevel(StrEnum):
    """Diary triage levels. Use ``rank`` (not the value) for ordering."""

    STABLE = "stable"
    ATTENTION = "attention"
    URGENT = "urgent"

    @property
    def rank(self) -> int:
        """0, 1, 2 — so ``max(levels, key=lambda l: l.rank)`` gives the most severe."""
        return _RANK[self.value]

    @property
    def label(self) -> str:
        """Russian name shown to doctors: «Стабильно», «Требует внимания», «Срочно»."""
        return _LABEL[self.value]


class UrgentScenario(StrEnum):
    """What an «Срочно» alert asks of the doctor (D-132)."""

    DANGER_NOW = "danger_now"  # «опасность сейчас»: a structured answer (questionnaire, diary safety question)
    CHECK_SOON = "check_soon"  # «уточнить состояние»: free text (keywords or the text model)

    @property
    def label(self) -> str:
        return {"danger_now": "Срочно — опасность сейчас", "check_soon": "Срочно — уточнить состояние"}[self.value]


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ComplaintRule(_Model):
    """Complaint codes that set a level; optionally only for patients on antidepressants."""

    complaints: tuple[str, ...]
    level: TriageLevel
    message: str
    only_on_antidepressant: bool = False


class KeywordLists(_Model):
    """Phrases for the keyword safety-net model (matched after ``normalize_name``)."""

    urgent: tuple[str, ...]
    attention: tuple[str, ...]


class UrgentScenarios(_Model):
    """Scenario of an urgent alert by where it came from. A scale's critical rule may override
    ``questionnaire`` with its own ``scenario``."""

    questionnaire: UrgentScenario  # a critical answer in a questionnaire
    diary_answers: UrgentScenario  # a structured diary answer (safety question, critical complaint)
    diary_text: UrgentScenario  # free text: keyword list or the text model


class DiaryCondition(_Model):
    """Facts about one v2 diary entry; every given field must hold. An unanswered field never
    matches (None ≠ any value)."""

    mood: tuple[Literal[0, 1, 2], ...] | None = None  # categories: 0 depressed, 1 normal, 2 elevated
    anxiety_min: int | None = Field(default=None, ge=0, le=3)
    irritability_min: int | None = Field(default=None, ge=0, le=3)
    sleep_hours_below: float | None = Field(default=None, gt=0, le=24)
    sleep_hours_above: float | None = Field(default=None, ge=0, lt=24)
    unusual_experiences: bool | None = None


class DiaryEntryRule(_Model):
    """One v2 entry matching ``when`` sets ``level``."""

    code: str
    when: DiaryCondition
    level: TriageLevel
    message: str


class DiaryStreakRule(_Model):
    """``when`` holds on ``days`` consecutive calendar days with v2 entries → ``level``."""

    code: str
    when: DiaryCondition
    days: int = Field(ge=2)
    level: TriageLevel
    message: str


class DiaryV2Rules(_Model):
    """Rules for diary v2 (mood 0–2, anxiety/irritability 0–3, sleep in hours). Pilot hypotheses."""

    entry_rules: tuple[DiaryEntryRule, ...] = ()
    streak_rules: tuple[DiaryStreakRule, ...] = ()


class TriageRules(_Model):
    """All configurable triage settings."""

    complaint_rules: tuple[ComplaintRule, ...]
    safety_question_unanswered: TriageLevel  # self-harm question skipped (None ≠ "no")
    side_effect_level: TriageLevel
    mood_drop_points: int  # mood drop of at least this many points ...
    mood_drop_days: int  # ... within this many days → ATTENTION
    model_unsure_threshold: float  # top probability below this → «Неоднозначно» (D-035)
    keywords: KeywordLists
    urgent_scenarios: UrgentScenarios
    diary_v2: DiaryV2Rules


def load_triage_rules(path: Path) -> TriageRules:
    """Parse ``triage.yaml``.

    Raises:
        DefinitionError: malformed file.
    """
    return validate_model(TriageRules, read_yaml(path), path.name)
