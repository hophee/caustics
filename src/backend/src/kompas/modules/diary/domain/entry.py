"""One diary entry (schema provisional: D-036 / A09)."""

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class Sleep(StrEnum):
    POOR = "poor"
    NORMAL = "normal"
    GOOD = "good"


@dataclass(frozen=True, slots=True)
class DiaryEntry:
    """Structured content of one entry, dated by the patient's local day.

    Version 2: mood categories 0–2, anxiety/irritability 0–3, sleep in hours, unusual experiences and
    the safety question ``self_harm_thoughts`` yes/no.
    Version 1 preserves historical 0–10 scores. None means not answered.
    """

    day: date
    mood: int | None
    anxiety: int | None
    energy: int | None
    sleep: Sleep | None
    complaints: frozenset[str]  # complaint codes from complaints.yaml
    side_effects: tuple[str, ...]
    self_harm_thoughts: bool | None
    text: str | None

    schema_version: int = 1
    irritability: int | None = None
    sleep_hours: float | None = None
    unusual_experiences: bool | None = None

    def problems(self, known_complaints: frozenset[str]) -> list[str]:
        """Validate the scales belonging to this diary version."""
        if type(self.schema_version) is not int or self.schema_version not in (1, 2):
            return ["Неизвестная версия дневника"]
        if self.schema_version == 2:
            problems = []
            for name, value, maximum in (
                ("настроение", self.mood, 2),
                ("тревога", self.anxiety, 3),
                ("раздражительность", self.irritability, 3),
            ):
                if value is not None and (type(value) is not int or not 0 <= value <= maximum):
                    problems.append(f"{name}: значение вне шкалы 0–{maximum}")
            if self.sleep_hours is not None and (
                type(self.sleep_hours) not in (int, float) or not 0 <= self.sleep_hours <= 24
            ):
                problems.append("Сон: укажите от 0 до 24 часов")
            if self.unusual_experiences is not None and type(self.unusual_experiences) is not bool:
                problems.append("Необычные проявления: нужен ответ да/нет")
            if self.self_harm_thoughts is not None and type(self.self_harm_thoughts) is not bool:
                problems.append("Мысли о самоповреждении: нужен ответ да/нет")
            if self.text is not None and (not isinstance(self.text, str) or len(self.text) > 4000):
                problems.append("Заметки: не более 4000 символов")
            return problems
        problems = [
            f"{name}: значение {value} вне шкалы 0–10"
            for name, value in (("настроение", self.mood), ("тревога", self.anxiety), ("энергия", self.energy))
            if value is not None and not 0 <= value <= 10
        ]
        problems += [f"неизвестная жалоба: {code}" for code in sorted(self.complaints - known_complaints)]
        return problems
