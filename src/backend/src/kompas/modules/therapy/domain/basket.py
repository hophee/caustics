"""The doctor's basket: current therapy (from the server) plus a draft (from the browser).

* The draft lives in the doctor's browser (D-040): new items and which current drugs to stop.
  "Remove" on a current drug in the UI is the stop action (D-043: add/remove only).
* Current therapy is **always supplied by the server** (confirmed prescriptions plus patient-reported
  drugs, D-050), never by the client, so removing a card in the browser can't hide its interactions
  unless the stop is part of what gets confirmed.
* Doses are normalised to milligrams at the boundary (phase 1 accepts mass units only).
"""

from dataclasses import dataclass
from datetime import date, time, timedelta
from decimal import Decimal
from typing import ClassVar, Literal

from kompas.core.errors import InvalidInputError, Problem


@dataclass(frozen=True, slots=True)
class KrSuggestion:
    """Added from the guideline-based selection; provenance is resolved by the server from the row."""

    kind: ClassVar[Literal["kr_suggestion"]] = "kr_suggestion"
    row_id: str


@dataclass(frozen=True, slots=True)
class Search:
    """Found by the doctor in the full drug base."""

    kind: ClassVar[Literal["search"]] = "search"


type Origin = KrSuggestion | Search
"""Where a new item came from (D-042). The client sends only this; the server derives the rest."""


@dataclass(frozen=True, slots=True)
class Intake:
    """One scheduled intake: local wall-clock time in the patient's time zone, and amount in mg."""

    at: time
    amount_mg: Decimal


@dataclass(frozen=True, slots=True)
class DailySchedule:
    """Every day at fixed times; intakes may have different amounts (25 mg morning, 50 mg evening)."""

    kind: ClassVar[Literal["daily"]] = "daily"
    intakes: tuple[Intake, ...]


@dataclass(frozen=True, slots=True)
class PrnSchedule:
    """As needed (при необходимости): no reminders, but a per-dose amount and a daily ceiling."""

    kind: ClassVar[Literal["prn"]] = "prn"
    dose_mg: Decimal
    max_doses_per_day: int


type Schedule = DailySchedule | PrnSchedule


@dataclass(frozen=True, slots=True)
class DoseRegimen:
    """How the doctor prescribes a drug. Duration is mandatory (D-047), also for PRN."""

    schedule: Schedule
    start: date
    duration_days: int

    @property
    def max_daily_mg(self) -> Decimal:
        """Daily: sum of intakes. PRN: ``dose_mg × max_doses_per_day``. Used by the dose check."""
        if isinstance(self.schedule, PrnSchedule):
            return self.schedule.dose_mg * self.schedule.max_doses_per_day
        return sum((intake.amount_mg for intake in self.schedule.intakes), Decimal(0))

    @property
    def end(self) -> date:
        """Last day of treatment (inclusive)."""
        return self.start + timedelta(days=self.duration_days - 1)

    def problems(self) -> list[str]:
        """Structural problems: non-positive duration or amounts, daily schedule without intakes,
        duplicate intake times, PRN without a positive ceiling. Empty list = valid."""
        problems: list[str] = []
        if self.duration_days <= 0:
            problems.append("длительность курса должна быть больше нуля (D-047)")
        if isinstance(self.schedule, DailySchedule):
            if not self.schedule.intakes:
                problems.append("не указано ни одного приёма")
            times = [intake.at for intake in self.schedule.intakes]
            if len(times) != len(set(times)):
                problems.append("время приёма повторяется")
            if any(intake.amount_mg <= 0 for intake in self.schedule.intakes):
                problems.append("доза приёма должна быть больше нуля")
        else:
            if self.schedule.dose_mg <= 0:
                problems.append("разовая доза должна быть больше нуля")
            if self.schedule.max_doses_per_day <= 0:
                problems.append("нужно указать максимум приёмов в сутки")
        return problems


def to_mg(amount: Decimal, unit: str) -> Decimal:
    """Convert a mass amount to milligrams (``мг``/``mg`` → ×1, ``г``/``g`` → ×1000, ``мкг`` → ÷1000).

    Raises:
        InvalidInputError: non-mass unit (e.g. "таблетка") or non-positive amount.
    """
    factors = {
        "мг": Decimal(1),
        "mg": Decimal(1),
        "г": Decimal(1000),
        "g": Decimal(1000),
        "мкг": Decimal("0.001"),
        "mcg": Decimal("0.001"),
    }
    factor = factors.get(unit.strip().lower().rstrip("."))
    if factor is None:
        raise InvalidInputError(
            f"Единица {unit!r} не поддерживается: укажите дозу в мг, г или мкг",
            (Problem("unit_unsupported", f"Единица {unit!r} не поддерживается", "unit"),),
        )
    if amount <= 0:
        raise InvalidInputError("Доза должна быть больше нуля", (Problem("amount_invalid", "Доза ≤ 0", "amount"),))
    return amount * factor


@dataclass(frozen=True, slots=True)
class CurrentTherapyItem:
    """A drug the patient takes now, as known to the server.

    ``ref`` is the server's id (prescription item id or patient-report id). Either ``label_id`` or
    ``substance`` is set (drugs prescribed elsewhere may be known only by name).
    """

    ref: str
    label_id: str | None
    substance: str | None
    regimen: DoseRegimen | None
    patient_reported: bool = False  # «со слов пациента», not yet confirmed (D-050)


@dataclass(frozen=True, slots=True)
class DraftItem:
    """A drug the doctor wants to add. ``key`` is the browser's stable id for the card."""

    key: str
    label_id: str
    regimen: DoseRegimen
    origin: Origin


@dataclass(frozen=True, slots=True)
class Draft:
    """What the browser sends: new items and refs of current items being stopped."""

    new_items: tuple[DraftItem, ...]
    stopped: frozenset[str] = frozenset()

    def problems(self, current: tuple[CurrentTherapyItem, ...]) -> list[Problem]:
        """Structural problems: duplicate keys, keys colliding with ``current:<ref>`` keys,
        stopped refs not in ``current``, invalid regimens. Empty list = valid."""
        problems: list[Problem] = []
        keys = [item.key for item in self.new_items]
        for key in sorted({k for k in keys if keys.count(k) > 1}):
            problems.append(Problem("duplicate_key", f"Ключ карточки {key!r} повторяется", key))
        for key in keys:
            if key.startswith(CURRENT_PREFIX):
                problems.append(Problem("reserved_key", f"Ключ {key!r} зарезервирован для текущей терапии", key))
        refs = {c.ref for c in current}
        for ref in sorted(self.stopped - refs):
            problems.append(Problem("unknown_stop", f"Отменяемый препарат {ref!r} не входит в текущую терапию", ref))
        for item in self.new_items:
            for message in item.regimen.problems():
                problems.append(Problem("regimen_invalid", message, f"{item.key}.regimen"))
        return problems


CURRENT_PREFIX = "current:"


@dataclass(frozen=True, slots=True)
class TherapyItem:
    """A drug in the resulting regimen, as the checks see it.

    ``key`` is the UI card id: the draft key for new items, ``"current:<ref>"`` for current ones.
    """

    key: str
    label_id: str | None
    substance: str | None
    regimen: DoseRegimen | None
    is_new: bool
    patient_reported: bool = False


def resulting_therapy(current: tuple[CurrentTherapyItem, ...], draft: Draft) -> tuple[TherapyItem, ...]:
    """Current items minus stopped ones, plus new items: what the patient would take after
    confirmation. This is what the checks run on.

    Raises:
        InvalidInputError: ``draft.problems(current)`` is not empty.
    """
    problems = draft.problems(current)
    if problems:
        raise InvalidInputError("Черновик назначения некорректен", tuple(problems))
    kept = tuple(
        TherapyItem(
            key=CURRENT_PREFIX + c.ref,
            label_id=c.label_id,
            substance=c.substance,
            regimen=c.regimen,
            is_new=False,
            patient_reported=c.patient_reported,
        )
        for c in current
        if c.ref not in draft.stopped
    )
    added = tuple(
        TherapyItem(key=i.key, label_id=i.label_id, substance=None, regimen=i.regimen, is_new=True)
        for i in draft.new_items
    )
    return kept + added
