"""КР documents and care standards behind each diagnosis (``kr/guidelines.json``, ``kr/standards.json``)."""

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from kompas.core.errors import DefinitionError
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.guidelines import load_guidelines

KR_DIR = Path(__file__).resolve().parents[1] / "knowledge_data" / "kr"


def test_every_diagnosis_has_its_sources(knowledge: Knowledge) -> None:
    g = knowledge.guidelines
    assert set(g.sources) == set(g.diagnoses)
    assert g.sources["F32.1"].guidelines == ("301_3",) and g.sources["F32.1"].standards == ()
    assert g.sources["F33.1"].guidelines == ("301_3",) and g.sources["F33.1"].standards == ("745н",)
    assert g.sources["F41.1"].guidelines == ("457_3",) and g.sources["F41.1"].standards == ()
    assert g.sources["F43.1"].standards == ("311н",)
    assert all(d.url.startswith("https://cr.minzdrav.gov.ru/preview-cr/") for d in g.documents)
    for row in g.rows:
        assert row.source.split()[1] in g.sources[row.code].guidelines, row.id


def _broken(tmp_path: Path, edit: Callable[[list[dict[str, Any]]], None], name: str = "diagnoses.json") -> Path:
    directory = tmp_path / "kr"
    shutil.copytree(KR_DIR, directory)
    data = json.loads((directory / name).read_text(encoding="utf-8"))
    edit(data)
    (directory / name).write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return directory


def test_unknown_document_reference_fails(tmp_path: Path) -> None:
    def edit(data: list[dict[str, Any]]) -> None:
        data[0]["standards"] = ["999н"]

    with pytest.raises(DefinitionError, match="неизвестные документы"):
        load_guidelines(_broken(tmp_path, edit))


def test_diagnosis_without_any_source_fails(tmp_path: Path) -> None:
    def edit(data: list[dict[str, Any]]) -> None:
        data[0]["guidelines"], data[0]["standards"] = [], []

    with pytest.raises(DefinitionError, match="ни КР, ни стандарт"):
        load_guidelines(_broken(tmp_path, edit))


def test_row_citing_another_guideline_fails(tmp_path: Path) -> None:
    def edit(data: list[dict[str, Any]]) -> None:
        row = next(r for r in data if r["code"] == "F41.1")
        row["src"] = "КР 301_3 (F32:F33 .pdf), п. 3.1.1"

    with pytest.raises(DefinitionError, match="цитирует КР 301_3"):
        load_guidelines(_broken(tmp_path, edit, "windows.json"))
