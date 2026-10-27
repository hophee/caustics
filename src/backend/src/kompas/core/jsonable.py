"""Turn domain objects (dataclasses, pydantic models, enums, dates, Decimals) into plain JSON values.

Used for JSONB snapshots (warnings, provenance, profile at confirmation) and by the CLI's ``--json``.
"""

import dataclasses
from datetime import date, datetime, time
from decimal import Decimal
from enum import Enum
from typing import Any
from zoneinfo import ZoneInfo


def to_jsonable(value: Any) -> Any:
    """Recursively convert ``value``; dict keys become strings, sets/tuples become lists."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        data = {f.name: to_jsonable(getattr(value, f.name)) for f in dataclasses.fields(value)}
        kind = getattr(type(value), "kind", None)  # tagged unions (Search / KrSuggestion, schedules)
        if isinstance(kind, str):
            data["kind"] = kind
        return data
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json")
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {str(k): to_jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set | frozenset):
        items = [to_jsonable(v) for v in value]
        return sorted(items, key=str) if isinstance(value, set | frozenset) else items
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, date | datetime | time):
        return value.isoformat()
    if isinstance(value, ZoneInfo):
        return value.key
    return value
