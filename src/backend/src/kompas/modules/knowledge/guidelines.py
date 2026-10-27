"""Clinical guidelines (КР) as data: ``knowledge_data/kr/*.json``.

Source: the team's КР summary table (extracted from prototype revision 6). Each guideline row says:
for diagnosis X at therapy stage Y (optionally under condition Z), the scheme uses a drug of role W,
from these classes/МНН, with this evidence grade, citing this clause. Rows have stable ids
(``kr-001``...) that baskets and prescriptions reference.
"""

import re
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from kompas.core.errors import DefinitionError, NotFoundError
from kompas.core.icd import IcdPattern
from kompas.core.text import normalize_name
from kompas.modules.knowledge._files import files_digest, read_json, validate_model


class ConditionKind(StrEnum):
    """How a row's condition is decided (column ``cc`` of the team table)."""

    NONE = "none"  # always applies
    STAGE = "stage"  # applies to the therapy stage only
    MIXED = "mixed"  # depends on the doctor's answer about mixed features
    FLAG = "flag"  # depends on an optional condition flag the doctor ticks


class _Model(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class SubstanceTarget(_Model):
    """A concrete МНН recommended by a row; off-label and evidence are per substance."""

    kind: Literal["substance"] = "substance"
    mnn: str  # normalised
    off_label: bool
    evidence: str | None  # this substance's УУР/УДД when the row lists several, else the row's


class ClassTarget(_Model):
    """A pharmacological class named by a row (expanded via ``DrugClass``)."""

    kind: Literal["class"] = "class"
    name: str
    evidence: str | None


class WindowTarget(_Model):
    """A whole clinical role («любой АД», «АВП»), expanded via ``WindowRule``."""

    kind: Literal["window"] = "window"
    window: str


GuidelineTarget = Annotated[SubstanceTarget | ClassTarget | WindowTarget, Field(discriminator="kind")]


class GuidelineRow(_Model):
    """One row of the КР table, with its drug list parsed once at load.

    ``substances_text`` stays as written in КР for display. ``targets`` is the parsed form.
    ``text_only`` rows are advice without a concrete drug (e.g. «увеличить дозу до максимальной или
    сменить АД»): shown to the doctor, never expanded into labels. ``unparsed`` lists fragments
    the parser could not map; they are reported as import warnings (fix them in
    ``kr/row_overrides.json``) instead of failing the whole load, because the table is still a team
    draft (C11).
    """

    id: str
    code: str  # ICD-10 code of the diagnosis (full code, never truncated)
    stage: str  # therapy stage / line as written in КР, e.g. "1 этап: купирующая", "2-я линия"
    condition: str  # e.g. "Смешанные черты"; empty = none
    condition_kind: ConditionKind
    scheme: str  # "Монотерапия", "Аугментация", ...
    window: str  # clinical role, e.g. "Антидепрессант"
    substances_text: str
    targets: tuple[GuidelineTarget, ...]
    text_only: bool
    unparsed: tuple[str, ...]
    evidence: str  # УУР/УДД as written, e.g. "A / 1" or "A / 2; B / 2"
    source: str  # document and clause, e.g. "КР 301_3 (F32:F33 .pdf), п. 3.1.1"
    quote: str


def parse_row_targets(
    substances_text: str,
    off_label_text: str,
    evidence_text: str,
    classes: tuple["DrugClass", ...],
    window_rules: tuple["WindowRule", ...],
    vocabulary: "Vocabulary | None" = None,
) -> tuple[tuple[GuidelineTarget, ...], bool, tuple[str, ...]]:
    """Parse a row's free-text drug list into ``(targets, text_only, unparsed)``.

    Fragments are split on ``;`` and ``,`` outside parentheses; ``Класс (a, b)`` yields the listed
    substances; class names and window names are matched against ``classes`` / ``window_rules``;
    remaining lower-case words are treated as МНН. Substances listed in ``off_label_text`` get
    ``off_label=True``. A row with no targets and recognisable advice wording is ``text_only``.
    """
    vocab = vocabulary or Vocabulary()
    off = {vocab.canonical(x) for x in re.split(r"[,;]", off_label_text) if x.strip()}
    class_by_name = {normalize_name(c.name): c for c in classes}
    window_names = {normalize_name(w.window): w.window for w in window_rules}
    groups = _split_top(substances_text, ";")
    evidence_parts = [e.strip() for e in evidence_text.split(";") if e.strip()]
    flat = [frag for group in groups for frag in _split_top(group, ",")]
    targets: list[GuidelineTarget] = []
    unparsed: list[str] = []
    position = 0
    for g_index, group in enumerate(groups):
        group_evidence = evidence_parts[g_index] if len(evidence_parts) == len(groups) > 1 else None
        for fragment in _split_top(group, ","):
            evidence = group_evidence or (evidence_parts[position] if len(evidence_parts) == len(flat) > 1 else None)
            position += 1
            pieces = [p for alt in _split_top(fragment, _OR) for p in re.split(r"\s+[/+]\s+", alt)]
            for piece in pieces:
                parsed = _parse_fragment(piece, class_by_name, window_names, off, evidence, vocab)
                if parsed is None:
                    unparsed.append(piece.strip())
                else:
                    targets.extend(parsed)
    return tuple(dict.fromkeys(targets)), not targets, tuple(u for u in unparsed if u)


class DrugClass(_Model):
    """A pharmacological class and its members, e.g. СИОЗС → сертралин, ..."""

    name: str
    atc: str | None
    substances: tuple[str, ...]
    source: str


class WindowRule(_Model):
    """How a clinical role maps to drugs when КР lists no МНН: ATC prefix plus add/exclude lists."""

    window: str
    atc_prefixes: tuple[str, ...]
    add_substances: tuple[str, ...]
    exclude_substances: tuple[str, ...]
    note: str


class RestrictionTrigger(_Model):
    """When a restriction applies (``kr/restriction_triggers.json``). All set fields must hold.
    ``mixed_features`` / ``stage_contains`` are not asked (D-122), so they make it «уточнить»; so does
    a trigger with no field set."""

    always: bool = False
    mixed_features: bool | None = None
    suicide_risk: str | None = None  # "high"
    pregnant: bool | None = None
    stage_contains: str | None = None


class Restriction(_Model):
    """A КР restriction applied after row selection (e.g. no antidepressant monotherapy with mixed
    features). Shown as a warning with its clause, never as hiding (A05).

    ``codes`` keeps the КР text («F32.x, F33.x»); ``icd_patterns`` is its parsed form.
    """

    codes: str
    icd_patterns: tuple[str, ...]
    condition: str
    rule: str
    source: str
    quote: str
    trigger: RestrictionTrigger = RestrictionTrigger()


class GuidelineDocument(_Model):
    """A КР document in the Минздрав rubricator (``kr/guidelines.json``), e.g. id ``301_3``."""

    id: str  # rubricator id: "<number>_<version>"
    title: str
    year: int  # year of approval
    url: str  # rubricator page


class CareStandard(_Model):
    """A Минздрав care standard (``kr/standards.json``): an order such as «745н»."""

    number: str
    date: date
    title: str
    url: str


class DiagnosisSources(_Model):
    """Which documents cover a diagnosis; either list may be empty, never both."""

    guidelines: tuple[str, ...]  # GuidelineDocument ids
    standards: tuple[str, ...]  # CareStandard numbers


class Vocabulary(_Model):
    """How КР text and label text are read (``kr/vocabulary.json``)."""

    substance_aliases: dict[str, str] = {}
    multiword_substances: tuple[str, ...] = ()
    not_a_drug: tuple[str, ...] = ()
    window_aliases: dict[str, str] = {}
    indication_keywords: dict[str, tuple[str, ...]] = {}  # ICD category → stems found in 4.1 text

    def canonical(self, name: str) -> str:
        """Normalised МНН with aliases resolved and markup (``**``) removed."""
        key = normalize_name(name.replace("*", ""))
        return self.substance_aliases.get(key, key)


class Guidelines(_Model):
    """The full КР knowledge for one version."""

    version: str
    vocabulary: Vocabulary = Vocabulary()
    diagnoses: dict[str, str]  # ICD code → name (the 21 MVP diagnoses)
    sources: dict[str, DiagnosisSources] = {}  # ICD code → КР documents and care standards
    documents: tuple[GuidelineDocument, ...] = ()
    standards: tuple[CareStandard, ...] = ()
    rows: tuple[GuidelineRow, ...]
    classes: tuple[DrugClass, ...]
    window_rules: tuple[WindowRule, ...]
    restrictions: tuple[Restriction, ...]

    def row(self, row_id: str) -> GuidelineRow:
        """Raises NotFoundError for an unknown id."""
        for row in self.rows:
            if row.id == row_id:
                return row
        raise NotFoundError(f"Неизвестная строка КР: {row_id}")

    def class_named(self, name: str) -> "DrugClass | None":
        """Class by (normalised) name, or None."""
        key = normalize_name(name)
        return next((c for c in self.classes if normalize_name(c.name) == key), None)

    def window_rule(self, window: str) -> "WindowRule | None":
        """Window rule by name, or None."""
        return next((w for w in self.window_rules if w.window == window), None)


def load_guidelines(directory: Path) -> tuple[Guidelines, list[str]]:
    """Read ``diagnoses.json`` (with each code's КР and care standards), ``guidelines.json`` (КР
    documents), ``standards.json`` (care standards), ``windows.json``, ``classes.json``,
    ``window_rules.json``, ``restrictions.json`` and optional ``row_overrides.json`` (row id →
    replacement targets).
    Version = ``"kr:" + sha256 of the files`` (first 12 hex chars).

    Returns the guidelines and import warnings (unparsed fragments per row).

    Raises:
        DefinitionError: missing/malformed files, duplicate row ids, rows with unknown diagnoses,
            unknown document references, a diagnosis with no source, a row citing another КР.
    """
    names = [
        "diagnoses.json",
        "guidelines.json",
        "standards.json",
        "windows.json",
        "classes.json",
        "window_rules.json",
        "restrictions.json",
    ]
    vocabulary_path = directory / "vocabulary.json"
    vocabulary = (
        validate_model(
            Vocabulary,
            {k: v for k, v in read_json(vocabulary_path).items() if not k.startswith("_")},
            "vocabulary.json",
        )
        if vocabulary_path.exists()
        else Vocabulary()
    )
    paths = [directory / n for n in names]
    overrides_path = directory / "row_overrides.json"
    raw = {n: read_json(path) for n, path in zip(names, paths, strict=True)}
    overrides: dict[str, Any] = read_json(overrides_path) if overrides_path.exists() else {}

    diagnoses = {d["code"]: d["name"] for d in raw["diagnoses.json"]}
    documents = tuple(validate_model(GuidelineDocument, d, "guidelines.json") for d in raw["guidelines.json"])
    standards = tuple(validate_model(CareStandard, s, "standards.json") for s in raw["standards.json"])
    sources = _diagnosis_sources(raw["diagnoses.json"], documents, standards)
    classes = tuple(
        validate_model(
            DrugClass,
            {
                "name": c["name"],
                "atc": c.get("atc") or None,
                "substances": [
                    vocabulary.canonical(x)
                    for x in re.split(r"[,;]", re.sub(r"\(.*?\)|–.*", "", c["mnn"]))
                    if x.strip()
                ],
                "source": c.get("source") or "",
            },
            "classes.json",
        )
        for c in raw["classes.json"]
    )
    window_rules = tuple(
        validate_model(
            WindowRule,
            {
                "window": w["window"],
                "atc_prefixes": [
                    a.strip().rstrip("*") for a in (w.get("atc") or "").split(",") if a.strip() not in ("", "—")
                ],
                "add_substances": [vocabulary.canonical(x) for x in (w.get("add_mnn") or "").split(",") if x.strip()],
                "exclude_substances": [
                    vocabulary.canonical(x) for x in (w.get("exclude_mnn") or "").split(",") if x.strip()
                ],
                "note": w.get("note") or "",
            },
            "window_rules.json",
        )
        for w in raw["window_rules.json"]
    )
    warnings: list[str] = []
    rows: list[GuidelineRow] = []
    seen: set[str] = set()
    for r in raw["windows.json"]:
        if r["id"] in seen:
            raise DefinitionError(f"windows.json: повторяется id {r['id']}")
        seen.add(r["id"])
        if r["code"] not in diagnoses:
            raise DefinitionError(f"windows.json: {r['id']} ссылается на неизвестный диагноз {r['code']}")
        if (m := _ROW_DOCUMENT.match(r.get("src") or "")) and m.group(1) not in sources[r["code"]].guidelines:
            raise DefinitionError(f"windows.json: {r['id']} цитирует КР {m.group(1)}, не указанные для {r['code']}")
        targets_data: list[Any]
        unparsed: tuple[str, ...]
        if r["id"] in overrides:
            override = overrides[r["id"]]
            targets_data = list(override.get("targets", []))
            text_only, unparsed = bool(override.get("text_only", False)), ()
        else:
            parsed, text_only, unparsed = parse_row_targets(
                r["mnn"], r.get("off") or "", r.get("uur") or "", classes, window_rules, vocabulary
            )
            targets_data = [t.model_dump() for t in parsed]
            warnings += [f"КР {r['id']} ({r['code']}, {r['stage']}): не распознано «{x}»" for x in unparsed]
        rows.append(
            validate_model(
                GuidelineRow,
                {
                    "id": r["id"],
                    "code": r["code"],
                    "stage": r["stage"],
                    "condition": r.get("cond") or "",
                    "condition_kind": r.get("cc") or "none",
                    "scheme": r.get("scheme") or "",
                    "window": r.get("win") or "",
                    "substances_text": r["mnn"],
                    "targets": targets_data,
                    "text_only": text_only,
                    "unparsed": list(unparsed),
                    "evidence": r.get("uur") or "",
                    "source": r.get("src") or "",
                    "quote": r.get("quote") or "",
                },
                f"windows.json:{r['id']}",
            )
        )
    triggers_path = directory / "restriction_triggers.json"
    triggers: dict[str, Any] = read_json(triggers_path) if triggers_path.exists() else {}
    restrictions = tuple(
        validate_model(
            Restriction,
            {
                **x,
                "icd_patterns": [
                    str(IcdPattern.parse(code.strip().replace(".x", "").replace(".X", "")))
                    for code in x["codes"].split(",")
                    if code.strip()
                ],
                "trigger": triggers.get(x["condition"], {}),
            },
            "restrictions.json",
        )
        for x in raw["restrictions.json"]
    )
    extra = [p for p in (overrides_path, triggers_path, vocabulary_path) if p.exists()]
    digest = files_digest([*paths, *extra])
    guidelines = Guidelines(
        version=f"kr:{digest}",
        vocabulary=vocabulary,
        diagnoses=diagnoses,
        sources=sources,
        documents=documents,
        standards=standards,
        rows=tuple(rows),
        classes=classes,
        window_rules=window_rules,
        restrictions=restrictions,
    )
    return guidelines, warnings


_ROW_DOCUMENT = re.compile(r"^КР (\d+_\d+)\b")  # «КР 301_3 (F32:F33 .pdf), п. 3.1.1» → 301_3


def _diagnosis_sources(
    raw: list[dict[str, Any]], documents: tuple[GuidelineDocument, ...], standards: tuple[CareStandard, ...]
) -> dict[str, DiagnosisSources]:
    """ICD code → its КР documents and care standards, every reference checked.

    Raises:
        DefinitionError: duplicate document ids, an unknown reference, or a diagnosis with no source.
    """
    document_ids = [d.id for d in documents]
    standard_numbers = [s.number for s in standards]
    if len(set(document_ids)) != len(document_ids) or len(set(standard_numbers)) != len(standard_numbers):
        raise DefinitionError("guidelines.json / standards.json: повторяется идентификатор документа")
    result: dict[str, DiagnosisSources] = {}
    for d in raw:
        entry = validate_model(
            DiagnosisSources,
            {"guidelines": d.get("guidelines", []), "standards": d.get("standards", [])},
            f"diagnoses.json:{d['code']}",
        )
        unknown = [g for g in entry.guidelines if g not in document_ids] + [
            s for s in entry.standards if s not in standard_numbers
        ]
        if unknown:
            raise DefinitionError(f"diagnoses.json: {d['code']} ссылается на неизвестные документы {unknown}")
        if not entry.guidelines and not entry.standards:
            raise DefinitionError(f"diagnoses.json: для {d['code']} не указаны ни КР, ни стандарт")
        result[d["code"]] = entry
    return result


_SUBSTANCE = re.compile(r"^[а-яё][а-яё\-]+$")


_OR = "\x00"  # placeholder separator for " или " at top level


def _split_top(text: str, sep: str) -> list[str]:
    """Split on ``sep`` outside parentheses (``_OR`` splits on the word «или»)."""
    if sep == _OR:
        text = re.sub(r"\s+или\s+", _OR, text)
    parts: list[str] = []
    current: list[str] = []
    depth = 0
    for ch in text:
        depth += ch == "("
        depth -= ch == ")"
        if ch == sep and depth == 0:
            parts.append("".join(current).strip())
            current = []
        else:
            current.append(" или " if ch == _OR else ch)
    parts.append("".join(current).strip())
    return [part for part in parts if part]


def _parse_fragment(
    fragment: str,
    class_by_name: dict[str, "DrugClass"],
    window_names: dict[str, str],
    off: set[str],
    evidence: str | None,
    vocab: "Vocabulary",
) -> list[GuidelineTarget] | None:
    """One fragment → targets, or None if it can't be mapped."""
    fragment = fragment.replace("*", "").strip().rstrip(".")
    fragment = re.sub(r"^(затем|далее|или)\s+", "", fragment)
    if m := re.match(r"^из\s+\S+\s+[–-]\s+(.+)$", fragment):  # «из СИОЗС – флувоксамин»
        fragment = m.group(1)
    elif " – " in fragment:  # «АВП – конкретные МНН в КР не названы ...»
        fragment = fragment.split(" – ", 1)[0]
    if m := re.match(r"^\S+:\s*(.+)$", fragment):  # «СИОЗС: пароксетин»
        fragment = m.group(1)
    fragment = re.sub(r"\s+в эффективной дозе$", "", fragment)
    fragment = re.sub(r"\s+\d+([.,]\d+)?\s*(мг|г)\b.*$", "", fragment)  # «прегабалин 150 мг однократно»
    head = re.sub(r"\s*\(.*?\)\s*", " ", fragment).strip()
    inside = re.findall(r"\((.*?)\)", fragment)
    key = normalize_name(head)
    if key in class_by_name:
        listed = [
            m
            for part in inside
            for m in (vocab.canonical(x) for x in part.split(","))
            if m in vocab.multiword_substances or _SUBSTANCE.match(m)
        ]
        if listed:
            return [SubstanceTarget(mnn=m, off_label=m in off, evidence=evidence) for m in listed]
        return [ClassTarget(name=class_by_name[key].name, evidence=evidence)]
    if key in window_names:
        return [WindowTarget(window=window_names[key])]
    if key in vocab.window_aliases:
        return [WindowTarget(window=vocab.window_aliases[key])]
    mnn = vocab.canonical(re.sub(r"^или\s+", "", head))
    if mnn in vocab.multiword_substances or (_SUBSTANCE.match(mnn) and mnn not in vocab.not_a_drug):
        return [SubstanceTarget(mnn=mnn, off_label=mnn in off, evidence=evidence)]
    return None
