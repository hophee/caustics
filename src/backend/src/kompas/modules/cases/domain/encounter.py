"""Validation of the new encounter record; legacy free-text visits remain readable."""

from typing import Any

from kompas.core.errors import InvalidInputError


def validate_encounter(content: dict[str, Any]) -> None:
    if content.get("schema") != "encounter-v1":
        return
    if content.get("status") not in {"draft", "completed"}:
        raise InvalidInputError("Неизвестный статус приёма")
    if content.get("status") != "completed":
        return
    if not content.get("diagnosis") or content.get("diagnosisSaved") is not True:
        raise InvalidInputError("Сохраните основной диагноз приёма")
    if content.get("risk") not in {"low", "high"}:
        raise InvalidInputError("Укажите актуальную оценку риска")
    if content.get("action") not in {"continue", "prescription", "dose", "diagnosis"}:
        raise InvalidInputError("Укажите решение по терапии")
    if content.get("action") != "continue" and not content.get("prescriptionId"):
        raise InvalidInputError("Подтвердите аптечку перед завершением приёма")
