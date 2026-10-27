"""Adherence/reminder policy (``rules/adherence.yaml``), psychiatrist-referral rules
(``rules/referrals.yaml``), adverse-event lists (``rules/adverse_events.yaml``), appointment
settings (``rules/appointments.yaml``) and document kinds/limits (``rules/documents.yaml``).

Both encode open questions (A07/C08 adherence formula and bands, A15 reminder defaults, B02 referral
rules), so every number is data: the team changes the file, not the code.
"""

from enum import StrEnum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from kompas.modules.knowledge._files import read_yaml, validate_model


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SkipReason(_Model):
    code: str
    label: str


class ReminderDefaults(_Model):
    """One reminder rule per dose (A15): first send at ``scheduled + first_offset_minutes``, then every
    ``repeat_every_minutes`` up to ``max_repeats`` more times, until the dose is marked."""

    enabled: bool = True
    first_offset_minutes: int = Field(0, ge=-120, le=120)
    repeat_every_minutes: int = Field(30, ge=5)
    max_repeats: int = Field(2, ge=0)


class ReminderLimits(_Model):
    """Bounds for what a patient may configure (frequency limit, A15)."""

    max_repeats: int = Field(5, ge=0)
    min_repeat_every_minutes: int = Field(10, ge=5)
    send_grace_minutes: int = Field(30, ge=1)  # a reminder later than this is dropped (e.g. after downtime)


class AdherenceRules(_Model):
    """How doses are classified and summarised for the doctor (spec §9, A07/C08 open).

    ``unmarked_counts_as_missed``: whether a past dose without any mark lowers the percentage (true)
    or is left out of it (false); unmarked doses are always shown separately (A12).
    ``green_from`` / ``yellow_from``: percent ≥ green_from is green, ≥ yellow_from yellow, else red.
    """

    late_after_minutes: int = Field(120, ge=1)
    mark_before_minutes: int = Field(120, ge=0)  # "taken" may be marked this early
    backfill_hours: int = Field(48, ge=0)  # how far back a patient may still mark a dose
    unmarked_counts_as_missed: bool = True
    green_from: float = Field(80, ge=0, le=100)
    yellow_from: float = Field(60, ge=0, le=100)
    summary_days: int = Field(7, ge=1)
    skip_streak_days: int = Field(3, ge=1)
    skip_reasons: tuple[SkipReason, ...]
    reminders: ReminderDefaults
    reminder_limits: ReminderLimits

    @model_validator(mode="after")
    def _bands(self) -> "AdherenceRules":
        if self.yellow_from > self.green_from:
            raise ValueError("yellow_from must not exceed green_from")
        return self

    def reason_codes(self) -> frozenset[str]:
        return frozenset(r.code for r in self.skip_reasons)


class ReferralCategory(StrEnum):
    """Spec §10: kept separate from the four urgency levels."""

    OBLIGATORY = "obligatory"  # «обязательно»
    DESIRABLE = "desirable"  # «желательно»

    @property
    def label(self) -> str:
        return {"obligatory": "обязательно", "desirable": "желательно"}[self.value]


class ReferralRule(_Model):
    """When the system recommends a psychiatrist referral (never issues one by itself).

    kinds: ``scale_level`` — a result of ``scale`` with band level ≥ ``min_level``;
    ``critical_answer`` — any critical answer on ``scale``; ``diary_urgent`` — a diary entry triaged
    «Срочно».
    """

    code: str
    category: ReferralCategory
    kind: Literal["scale_level", "critical_answer", "diary_urgent"]
    scale: str | None = None
    min_level: int | None = Field(None, ge=0, le=4)
    reason: str

    @model_validator(mode="after")
    def _fields(self) -> "ReferralRule":
        if self.kind in ("scale_level", "critical_answer") and not self.scale:
            raise ValueError(f"{self.code}: kind {self.kind} needs 'scale'")
        if self.kind == "scale_level" and self.min_level is None:
            raise ValueError(f"{self.code}: kind scale_level needs 'min_level'")
        return self


class ReferralRules(_Model):
    rules: tuple[ReferralRule, ...] = ()


class AeSeverity(_Model):
    """A patient-facing severity and the alert level it raises (None = no alert, only a notification)."""

    code: Literal["mild", "moderate", "severe"]
    label: str
    alert: Literal["urgent", "high", "planned"] | None = None


class AeSymptom(_Model):
    code: str
    label: str


class AdverseEventRules(_Model):
    """``rules/adverse_events.yaml`` (spec §9). ``other`` must be among the symptoms (free text)."""

    severities: tuple[AeSeverity, ...]
    symptoms: tuple[AeSymptom, ...]
    report_portal: str

    @model_validator(mode="after")
    def _complete(self) -> "AdverseEventRules":
        if {s.code for s in self.severities} != {"mild", "moderate", "severe"}:
            raise ValueError("severities must define mild, moderate and severe")
        if "other" not in {s.code for s in self.symptoms}:
            raise ValueError("symptoms must include 'other'")
        return self

    def severity(self, code: str) -> AeSeverity:
        return next(s for s in self.severities if s.code == code)

    def symptom_label(self, code: str) -> str | None:
        return next((s.label for s in self.symptoms if s.code == code), None)


class AppointmentReminder(_Model):
    code: str
    before_minutes: int = Field(ge=5)


class AppointmentRules(_Model):
    """``rules/appointments.yaml`` (spec §11 «Приёмы»): reminder offsets and booking limits."""

    reminders: tuple[AppointmentReminder, ...]
    send_grace_minutes: int = Field(30, ge=1)
    default_duration_minutes: int = Field(30, ge=5, le=480)
    plan_ahead_days: int = Field(365, ge=1)
    request_until_minutes: int = Field(120, ge=0)

    @model_validator(mode="after")
    def _unique(self) -> "AppointmentRules":
        if len({r.code for r in self.reminders}) != len(self.reminders):
            raise ValueError("reminder codes must be unique")
        return self


class DocumentKind(_Model):
    code: str
    label: str


class DocumentRules(_Model):
    """``rules/documents.yaml`` (spec §11): upload limits and the document kinds of each side.
    ``prescription`` must be a doctor kind (it is built from a confirmed prescription version)."""

    max_upload_mb: int = Field(10, ge=1, le=50)
    content_types: tuple[Literal["application/pdf", "image/jpeg", "image/png"], ...]
    patient_kinds: tuple[DocumentKind, ...]
    doctor_kinds: tuple[DocumentKind, ...]

    @model_validator(mode="after")
    def _kinds(self) -> "DocumentRules":
        codes = [k.code for k in (*self.patient_kinds, *self.doctor_kinds)]
        if len(set(codes)) != len(codes):
            raise ValueError("document kind codes must be unique across both sides")
        if "prescription" not in {k.code for k in self.doctor_kinds}:
            raise ValueError("doctor_kinds must include 'prescription'")
        return self

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def label(self, code: str) -> str | None:
        return next((k.label for k in (*self.patient_kinds, *self.doctor_kinds) if k.code == code), None)


def load_document_rules(path: Path) -> DocumentRules:
    """Parse ``documents.yaml``. Raises DefinitionError for a malformed file."""
    return validate_model(DocumentRules, read_yaml(path), path.name)


def load_appointment_rules(path: Path) -> AppointmentRules:
    """Parse ``appointments.yaml``. Raises DefinitionError for a malformed file."""
    return validate_model(AppointmentRules, read_yaml(path), path.name)


def load_adverse_event_rules(path: Path) -> AdverseEventRules:
    """Parse ``adverse_events.yaml``. Raises DefinitionError for a malformed file."""
    return validate_model(AdverseEventRules, read_yaml(path), path.name)


def load_adherence_rules(path: Path) -> AdherenceRules:
    """Parse ``adherence.yaml``. Raises DefinitionError for a malformed file."""
    return validate_model(AdherenceRules, read_yaml(path), path.name)


def load_referral_rules(path: Path) -> ReferralRules:
    """Parse ``referrals.yaml``. Raises DefinitionError for a malformed file."""
    return validate_model(ReferralRules, read_yaml(path), path.name)
