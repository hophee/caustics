"""Convert plain JSON (CLI example files, later API payloads) into domain objects.

Kept separate from the domain so the API layer can reuse the same conversions. Every function
raises ``InvalidInputError`` with a pointer to the offending field.
"""

from collections.abc import Mapping
from datetime import date, time
from decimal import Decimal, InvalidOperation
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from kompas.core.errors import InvalidInputError, NotFoundError, Problem
from kompas.core.icd import IcdCode
from kompas.modules.cases.domain.profile import PatientProfile, Sex, SuicideRisk, Tristate
from kompas.modules.diary.domain.entry import DiaryEntry, Sleep
from kompas.modules.knowledge.drugs import DrugCatalog
from kompas.modules.therapy.domain.basket import (
    CurrentTherapyItem,
    DailySchedule,
    DoseRegimen,
    Draft,
    DraftItem,
    Intake,
    KrSuggestion,
    Origin,
    PrnSchedule,
    Search,
    to_mg,
)

_MISSING = object()


def _fail(message: str, pointer: str) -> InvalidInputError:
    return InvalidInputError(message, (Problem("invalid_input", message, pointer),))


def _get(data: Mapping[str, Any], key: str, pointer: str, default: Any = _MISSING) -> Any:
    if key in data:
        return data[key]
    if default is _MISSING:
        raise _fail(f"Нет обязательного поля {key!r}", f"{pointer}.{key}")
    return default


def _date(value: Any, pointer: str) -> date:
    try:
        return date.fromisoformat(str(value))
    except ValueError:
        raise _fail(f"Ожидается дата ГГГГ-ММ-ДД, получено {value!r}", pointer) from None


def _enum[E](enum: type[E], value: Any, pointer: str) -> E:
    try:
        return enum(value)  # type: ignore[call-arg]
    except ValueError:
        allowed = ", ".join(str(e.value) for e in enum)  # type: ignore[attr-defined]
        raise _fail(f"Значение {value!r} не из: {allowed}", pointer) from None


def _list_or_none(data: Mapping[str, Any], key: str, pointer: str) -> tuple[str, ...] | None:
    """List facts must be given explicitly: a list (possibly empty) or null for "not asked"."""
    value = _get(data, key, pointer)
    if value is None:
        return None
    if not isinstance(value, list):
        raise _fail(f"{key}: ожидается список или null", f"{pointer}.{key}")
    return tuple(str(v) for v in value)


def parse_patient(data: Mapping[str, Any], pointer: str = "patient") -> PatientProfile:
    """Patient profile. List facts (allergies, ...) are required: ``[]`` = none, ``null`` = not asked."""
    try:
        tz = ZoneInfo(str(data.get("timezone", "Europe/Moscow")))
    except ZoneInfoNotFoundError:
        raise _fail(f"Неизвестный часовой пояс {data.get('timezone')!r}", f"{pointer}.timezone") from None
    return PatientProfile(
        birth_date=_date(_get(data, "birth_date", pointer), f"{pointer}.birth_date"),
        sex=_enum(Sex, _get(data, "sex", pointer), f"{pointer}.sex"),
        timezone=tz,
        pregnant=_enum(Tristate, data.get("pregnant", "unknown"), f"{pointer}.pregnant"),
        lactating=_enum(Tristate, data.get("lactating", "unknown"), f"{pointer}.lactating"),
        substance_use=_enum(Tristate, data.get("substance_use", "unknown"), f"{pointer}.substance_use"),
        suicide_risk=_enum(SuicideRisk, data.get("suicide_risk", "unknown"), f"{pointer}.suicide_risk"),
        diagnoses=tuple(IcdCode.parse(str(c)) for c in data.get("diagnoses", [])),
        allergies=_list_or_none(data, "allergies", pointer),
        intolerant_excipients=_list_or_none(data, "intolerant_excipients", pointer),
        past_intolerance=_list_or_none(data, "past_intolerance", pointer),
        past_inefficacy=_list_or_none(data, "past_inefficacy", pointer),
        weight_kg=data.get("weight_kg"),
    )


def resolve_label(data: Mapping[str, Any], catalog: DrugCatalog, pointer: str) -> str:
    """``label_id`` as given, or ``drug`` = an МНН / trade name resolved to its first search hit
    (a CLI convenience; the real client always sends label ids)."""
    if "label_id" in data:
        return str(data["label_id"])
    query = str(_get(data, "drug", pointer))
    by_substance = catalog.by_substance(query)
    if by_substance:
        return by_substance[0].id
    page = catalog.search(query, limit=1)
    if not page.items:
        raise NotFoundError(f"Препарат {query!r} не найден в базе ОХЛП", (Problem("not_found", query, pointer),))
    return page.items[0].id


def _decimal(value: Any, pointer: str) -> Decimal:
    try:
        return Decimal(str(value))
    except InvalidOperation:
        raise _fail(f"Ожидается число, получено {value!r}", pointer) from None


def parse_regimen(data: Mapping[str, Any], pointer: str) -> DoseRegimen:
    """``{"intakes": [{"at": "08:00", "amount": 50, "unit": "мг"}], "start": ..., "duration_days": 28}``
    or ``{"prn": {"dose": 0.5, "unit": "мг", "max_per_day": 2}, ...}``."""
    unit_default = str(data.get("unit", "мг"))
    if "prn" in data:
        prn = data["prn"]
        schedule: DailySchedule | PrnSchedule = PrnSchedule(
            dose_mg=to_mg(
                _decimal(_get(prn, "dose", f"{pointer}.prn"), f"{pointer}.prn.dose"), str(prn.get("unit", unit_default))
            ),
            max_doses_per_day=int(_get(prn, "max_per_day", f"{pointer}.prn")),
        )
    else:
        intakes = []
        for i, raw in enumerate(_get(data, "intakes", pointer)):
            where = f"{pointer}.intakes[{i}]"
            try:
                at = time.fromisoformat(str(_get(raw, "at", where)))
            except ValueError:
                raise _fail(f"Ожидается время ЧЧ:ММ, получено {raw.get('at')!r}", f"{where}.at") from None
            amount = to_mg(_decimal(_get(raw, "amount", where), f"{where}.amount"), str(raw.get("unit", unit_default)))
            intakes.append(Intake(at, amount))
        schedule = DailySchedule(tuple(intakes))
    return DoseRegimen(
        schedule=schedule,
        start=_date(_get(data, "start", pointer), f"{pointer}.start"),
        duration_days=int(_get(data, "duration_days", pointer)),
    )


def parse_origin(data: Mapping[str, Any] | None, pointer: str) -> Origin:
    """``{"kind": "search"}`` (default) or ``{"kind": "kr_suggestion", "row_id": "kr-098"}``."""
    if not data or data.get("kind", "search") == "search":
        return Search()
    if data.get("kind") == "kr_suggestion":
        return KrSuggestion(row_id=str(_get(data, "row_id", pointer)))
    raise _fail(f"Неизвестный источник {data.get('kind')!r}", f"{pointer}.kind")


def parse_current(items: list[Mapping[str, Any]], catalog: DrugCatalog) -> tuple[CurrentTherapyItem, ...]:
    """Current therapy as the server would supply it (CLI files list it explicitly)."""
    result = []
    for i, raw in enumerate(items):
        where = f"current_therapy[{i}]"
        has_label = "label_id" in raw or "drug" in raw
        result.append(
            CurrentTherapyItem(
                ref=str(_get(raw, "ref", where)),
                label_id=resolve_label(raw, catalog, where) if has_label else None,
                substance=raw.get("substance"),
                regimen=parse_regimen(raw["regimen"], f"{where}.regimen") if "regimen" in raw else None,
                patient_reported=bool(raw.get("patient_reported", False)),
            )
        )
    return tuple(result)


def parse_draft(data: Mapping[str, Any], catalog: DrugCatalog) -> Draft:
    """``{"new_items": [{"key", "drug" | "label_id", "regimen", "origin"}], "stopped": ["ref"]}``."""
    items = []
    for i, raw in enumerate(data.get("new_items", [])):
        where = f"draft.new_items[{i}]"
        items.append(
            DraftItem(
                key=str(_get(raw, "key", where)),
                label_id=resolve_label(raw, catalog, where),
                regimen=parse_regimen(_get(raw, "regimen", where), f"{where}.regimen"),
                origin=parse_origin(raw.get("origin"), f"{where}.origin"),
            )
        )
    return Draft(new_items=tuple(items), stopped=frozenset(str(s) for s in data.get("stopped", [])))


def parse_diary_entry(data: Mapping[str, Any], pointer: str = "entry") -> DiaryEntry:
    """Diary entry; ``self_harm_thoughts`` may be true/false/null (null = not answered)."""
    sleep = data.get("sleep")
    return DiaryEntry(
        day=_date(_get(data, "day", pointer), f"{pointer}.day"),
        schema_version=data.get("schema_version", 1),
        irritability=data.get("irritability"),
        sleep_hours=data.get("sleep_hours"),
        unusual_experiences=data.get("unusual_experiences"),
        mood=data.get("mood"),
        anxiety=data.get("anxiety"),
        energy=data.get("energy"),
        sleep=_enum(Sleep, sleep, f"{pointer}.sleep") if sleep is not None else None,
        complaints=frozenset(str(c) for c in data.get("complaints", [])),
        side_effects=tuple(str(s) for s in data.get("side_effects", [])),
        self_harm_thoughts=data.get("self_harm_thoughts"),
        text=data.get("text"),
    )
