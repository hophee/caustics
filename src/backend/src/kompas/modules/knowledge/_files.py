"""Shared helpers for reading knowledge files and turning schema errors into ``DefinitionError``."""

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ValidationError

from kompas.core.errors import DefinitionError, Problem


def read_yaml(path: Path) -> dict[str, Any]:
    """Read a YAML file whose top level is a mapping.

    Raises:
        DefinitionError: the file can't be read, isn't valid YAML, or isn't a mapping.
    """
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DefinitionError(f"Не удалось прочитать {path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise DefinitionError(f"Некорректный YAML в {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise DefinitionError(f"{path}: ожидается словарь на верхнем уровне")
    return data


def read_json(path: Path) -> Any:
    """Read a JSON file.

    Raises:
        DefinitionError: the file can't be read or isn't valid JSON.
    """
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise DefinitionError(f"Не удалось прочитать {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise DefinitionError(f"Некорректный JSON в {path}: {exc}") from exc


def validate_model[M: BaseModel](model: type[M], data: object, where: str) -> M:
    """``model.model_validate(data)`` with pydantic errors converted to a ``DefinitionError``."""
    try:
        return model.model_validate(data)
    except ValidationError as exc:
        details = tuple(
            Problem(code="schema", message=err["msg"], pointer=".".join(str(p) for p in err["loc"]))
            for err in exc.errors()
        )
        raise DefinitionError(f"{where}: файл не соответствует схеме", details) from exc


def files_digest(paths: Iterable[Path]) -> str:
    """First 12 hex chars of a sha256 over the files' contents (in the given order)."""
    digest = hashlib.sha256()
    for path in paths:
        digest.update(path.read_bytes())
    return digest.hexdigest()[:12]
