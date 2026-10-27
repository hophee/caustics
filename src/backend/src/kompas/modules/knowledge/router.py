"""Read-only knowledge lookups the screens need: scale forms, complaint catalog, diagnosis names.

Scale forms expose items and answer options only — never bands, thresholds or critical rules, so a
patient client can't derive a score interpretation from them.
"""

import re
from typing import Any

from fastapi import APIRouter, Query

from kompas.api.deps import ActorDep, DoctorDep, KnowledgeDep
from kompas.core.errors import NotFoundError
from kompas.core.icd import IcdCode
from kompas.modules.knowledge.scales import SumScoring
from kompas.modules.therapy.domain.selection import diagnosis_rows

router = APIRouter(tags=["knowledge"])


@router.get("/knowledge/version")
async def get_version(knowledge: KnowledgeDep, _actor: ActorDep) -> dict[str, Any]:
    """Knowledge version and whether the drug catalog is loaded."""
    return {"version": knowledge.version, "drugs_loaded": knowledge.drugs is not None}


@router.get("/scales")
async def get_scales(knowledge: KnowledgeDep, actor: ActorDep) -> list[dict[str, Any]]:
    """Scales the caller can fill (patients: self-report scales; doctors: all)."""
    return [
        {
            "code": s.code,
            "name": s.name,
            "filled_by": s.filled_by.value,
            "items": len(s.items),
            **({"diagnoses": list(s.diagnoses), "source": s.source} if actor.is_doctor else {}),
        }
        for s in sorted(knowledge.scales.values(), key=lambda s: s.code)
        if actor.is_doctor or s.filled_by.value != "clinician"
    ]


@router.get("/scales/{code}")
async def get_scale_form(code: str, knowledge: KnowledgeDep, actor: ActorDep) -> dict[str, Any]:
    """The form to fill: items with answer options."""
    scale = knowledge.scales.get(code)
    if scale is None or (actor.is_patient and scale.filled_by.value == "clinician"):
        raise NotFoundError(f"Шкала {code!r} не найдена")
    simple_sum = (
        isinstance(scale.scoring, SumScoring)
        and not scale.scoring.reverse
        and scale.scoring.items is None
        and scale.scoring.bands_basis == "raw"
        and all(i.scored and not i.show_if and i.options for i in scale.items)
    )
    return {
        "code": scale.code,
        "name": scale.name,
        "version": scale.version,
        "filled_by": scale.filled_by.value,
        "instructions": scale.instructions,
        **(
            {
                "notes": scale.notes,
                "references": scale.references,
                "simple_sum": simple_sum,
                "max_total": sum(max(o.value for o in i.options) for i in scale.items) if simple_sum else None,
            }
            if actor.is_doctor
            else {}
        ),
        "items": [
            {
                "id": item.id,
                "text": item.text,
                "hint": item.hint,
                "section": item.section,
                "input": item.input,
                "min": item.min,
                "max": item.max,
                "show_if": item.show_if.model_dump(mode="json") if item.show_if else None,
                "options": [{"value": o.value, "label": o.label} for o in item.options],
            }
            for item in scale.items
        ],
    }


@router.get("/complaints")
async def get_complaints(knowledge: KnowledgeDep, _actor: ActorDep) -> dict[str, Any]:
    """The diary complaint catalog (codes and labels)."""
    data: dict[str, Any] = knowledge.complaints.model_dump(mode="json")
    return data


@router.get("/guidelines/diagnoses")
async def get_guideline_diagnoses(knowledge: KnowledgeDep, _actor: DoctorDep) -> list[dict[str, Any]]:
    """The diagnoses of the knowledge base with links to the КР and care standards that cover them.
    A source that doesn't exist for a diagnosis is simply not listed."""
    documents = {d.id: d for d in knowledge.guidelines.documents}
    standards = {s.number: s for s in knowledge.guidelines.standards}
    sources = knowledge.guidelines.sources
    return [
        {
            "code": code,
            "name": name,
            "guidelines": [documents[g].model_dump(mode="json") for g in sources[code].guidelines],
            "standards": [standards[s].model_dump(mode="json") for s in sources[code].standards],
        }
        for code, name in sorted(knowledge.guidelines.diagnoses.items())
    ]


@router.get("/guidelines/diagnosis")
async def get_guideline_diagnosis(
    knowledge: KnowledgeDep, _actor: DoctorDep, diagnosis: str = Query(...)
) -> dict[str, Any]:
    """A diagnosis covered by the guidelines: its name (404 if there are no КР for it)."""
    code = IcdCode.parse(diagnosis)
    diagnosis_rows(code, knowledge.guidelines)
    return {"diagnosis": code.code, "name": knowledge.guidelines.diagnoses.get(code.code)}


@router.get("/knowledge/diagnoses")
async def diagnoses_search(
    knowledge: KnowledgeDep, _actor: DoctorDep, q: str = "", scope: str = "main"
) -> list[dict[str, str]]:
    names = dict(knowledge.guidelines.diagnoses)
    contexts: dict[str, str] = {}
    if scope == "contra":
        names = {}
        if knowledge.drugs is not None:
            for label in knowledge.drugs.labels():
                for code in label.contraindicated_icd.value:
                    match = re.fullmatch(r"([A-Z])(\d{2})-\1(\d{2})", code)
                    codes = [f"{match[1]}{i:02d}" for i in range(int(match[2]), int(match[3]) + 1)] if match else [code]
                    for item in codes:
                        names.setdefault(item, knowledge.guidelines.diagnoses.get(item, item))
                        contexts.setdefault(item, label.contraindicated_icd.quote or "")
    needle = q.casefold().strip()
    return [
        {"code": code, "name": name, "context": contexts.get(code, "")}
        for code, name in names.items()
        if not needle or needle in f"{code} {name} {contexts.get(code, '')}".casefold()
    ][:50]
