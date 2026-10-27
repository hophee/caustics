"""Drug labels (ОХЛП) as structured, sourced facts, and an in-memory versioned catalog.

One ``DrugLabel`` = one official label file: a specific trade name and form. Every safety fact keeps
the quote and section it was extracted from, so each warning can cite its source (D-016).
Source data: ``ohlp_fields_v4.csv`` (local, not in git).

The catalog is an immutable snapshot of one knowledge version. Checks depend only on the small
``DrugLookup`` protocol. The service layer (next phase) loads the snapshot from Postgres
asynchronously at startup or when the knowledge version changes; 866 psychiatric labels fit in
memory easily.
"""

import csv
import hashlib
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from kompas.core.errors import DefinitionError, NotFoundError
from kompas.core.text import normalize_name


class Status(StrEnum):
    """Normalised value of a label's safety field (pregnancy, renal, elderly, ...)."""

    CONTRAINDICATED = "contraindicated"
    CAUTION = "caution"
    DOSE_ADJUSTMENT = "dose_adjustment"
    NO_ADJUSTMENT = "no_adjustment"
    NOT_AFFECTED = "not_affected"  # e.g. driving: «не влияет»
    NO_DATA = "no_data"


_STATUS_WORDS = {
    "противопоказано": Status.CONTRAINDICATED,
    "с осторожностью": Status.CAUTION,
    "коррекция дозы": Status.DOSE_ADJUSTMENT,
    "без коррекции": Status.NO_ADJUSTMENT,
    "не влияет": Status.NOT_AFFECTED,
    "нет данных": Status.NO_DATA,
    "": Status.NO_DATA,
}

_NO_DATA = {"", "нет данных", "nan"}


@dataclass(frozen=True, slots=True)
class Fact[T]:
    """A value extracted from a label, with the sentence and section it came from."""

    value: T
    quote: str | None = None
    section: str | None = None


@dataclass(frozen=True, slots=True)
class DoseInfo:
    """Dosing from section 4.2.

    The ``*_text`` facts are the raw phrases shown to the doctor (A04: recommend from the label,
    never invent numbers). ``max_daily_mg`` is set only when the maximum daily dose parses
    unambiguously into milligrams; otherwise the dose check emits a "not verifiable" entry.
    """

    start_text: Fact[str | None]
    max_text: Fact[str | None]
    frequency_text: Fact[str | None]
    max_daily_mg: Decimal | None


@dataclass(frozen=True, slots=True)
class DrugLabel:
    """Safety-relevant content of one label.

    ``id`` = first 16 hex chars of sha1 of the label file name: stable across re-imports, stored in
    prescriptions and warning snapshots.
    ``contraindicated_icd`` is backed by ``contra_conditions_quote/_sec`` (4.3).
    ``caution_conditions`` (4.4) is free text only: shown on the card, not matched (no codes in data).
    ``groups`` are the interaction groups the drug belongs to (extraction + ``group_membership``);
    ``contraindicated_with`` / ``caution_with`` are groups it must not / should carefully be combined
    with (4.5). Patient-level pseudo-groups such as «алкоголь» are kept out of these sets and stored
    in ``patient_level_contraindicated`` / ``patient_level_caution`` (checked against the profile).
    """

    id: str
    trade_name: str
    form: str
    atc: str | None
    substances: tuple[str, ...]  # normalised МНН; several for combination products
    groups: frozenset[str]
    indications: Fact[str | None]  # 4.1 text
    min_age: Fact[int | None]
    pregnancy: Fact[Status]
    lactation: Fact[Status]
    elderly: Fact[Status]
    renal: Fact[Status]
    renal_gfr_threshold: Fact[str | None]  # raw, e.g. "60"
    hepatic: Fact[Status]
    hepatic_child_pugh: Fact[str | None]  # raw, e.g. "A; B; C"
    driving: Fact[Status]
    contraindicated_icd: Fact[tuple[str, ...]]  # ICD patterns
    caution_conditions: Fact[str | None]
    contraindicated_with: Fact[frozenset[str]]
    caution_with: Fact[frozenset[str]]
    patient_level_contraindicated: Fact[frozenset[str]]  # e.g. {"алкоголь"} from int_contra
    patient_level_caution: Fact[frozenset[str]]  # from int_caution
    named_interactions: Fact[tuple[str, ...]]  # МНН named in 4.5 (self excluded)
    excipients: Fact[tuple[str, ...]]
    dose: DoseInfo
    antidepressant_profile: str | None  # «седативный» / «активирующий» / «сбалансированный»
    needs_check: bool
    needs_check_reason: str | None
    source_file: str

    @property
    def display_name(self) -> str:
        """``"Сертралин (сертралин), таблетки"``; falls back to the file name if the trade name is
        missing in the data."""
        parts = Path(self.source_file).name.split("_")
        name = self.trade_name or (parts[1] if len(parts) > 1 else self.source_file)
        parts = [name, f"({', '.join(self.substances)})" if self.substances else ""]
        text = " ".join(p for p in parts if p)
        return f"{text}, {self.form}" if self.form else text

    @property
    def is_combination(self) -> bool:
        """True for labels with several active substances (dose checks are not verifiable)."""
        return len(self.substances) > 1


@dataclass(frozen=True, slots=True)
class Page[T]:
    """One page of results."""

    items: tuple[T, ...]
    total: int
    offset: int
    limit: int


@dataclass(frozen=True, slots=True)
class Vocabulary:
    """Pickable names from the catalog (normalised МНН and excipients; trade names as written)."""

    substances: tuple[str, ...]
    trade_names: tuple[tuple[str, tuple[str, ...]], ...]  # (trade name, its МНН)
    excipients: tuple[str, ...]


class DrugLookup(Protocol):
    """What the safety checks need from a catalog."""

    @property
    def version(self) -> str:
        """Knowledge version of this snapshot."""
        ...

    def get(self, label_id: str) -> DrugLabel:
        """Raises NotFoundError for an unknown id."""
        ...

    def groups_of_substance(self, substance: str) -> frozenset[str]:
        """Interaction groups of an МНН (from labels or the dictionary); empty if unknown."""
        ...


@dataclass(frozen=True, slots=True)
class MedicationName:
    """Identity from any CSV row; label_id exists only when safety facts were parsed."""

    id: str
    trade_name: str
    form: str
    substances: tuple[str, ...]
    label_id: str | None


class DrugCatalog:
    """Immutable in-memory snapshot of drug labels for one knowledge version.

    Also holds a substance → interaction-groups dictionary built from *all* CSV rows, so current
    therapy without an in-scope label (e.g. tramadol) can still be checked.
    """

    def __init__(
        self,
        version: str,
        labels: Iterable[DrugLabel],
        substance_groups: Mapping[str, frozenset[str]],
        medications: Iterable[MedicationName] = (),
    ) -> None:
        """Index labels by id and normalised substance.

        Raises:
            DefinitionError: duplicate label ids.
        """
        self._version = version
        self._by_id: dict[str, DrugLabel] = {}
        self._by_substance: dict[str, list[DrugLabel]] = {}
        for label in labels:
            if label.id in self._by_id:
                raise DefinitionError(f"Повторяющийся идентификатор ОХЛП {label.id} ({label.source_file})")
            self._by_id[label.id] = label
            if len(label.substances) == 1:
                self._by_substance.setdefault(label.substances[0], []).append(label)
        self._groups = {normalize_name(k): v for k, v in substance_groups.items()}
        self._medications = {m.id: m for m in medications}
        for label in self._by_id.values():
            self._medications[label.id] = MedicationName(
                label.id, label.trade_name, label.form, label.substances, label.id
            )

    def medication_substances(self) -> dict[str, str]:
        """Canonical names and complete combinations allowed for actual medication use."""
        names = {s for m in self._medications.values() for s in m.substances}
        names.update(" + ".join(m.substances) for m in self._medications.values() if m.substances)
        return {normalize_name(s): s for s in names}

    def search_medications(self, query: str, *, limit: int = 50) -> tuple[list[dict[str, object]], int]:
        """Search the entire identity directory, keeping it separate from parsed safety labels."""
        q = normalize_name(query)
        if not q:
            return [], 0
        choices: list[tuple[int, str, dict[str, object]]] = []
        for name in sorted({s for m in self._medications.values() for s in m.substances}):
            if q in name:
                rank = 0 if name == q else 2 if name.startswith(q) else 4
                choices.append((rank, name, {"code": f"mnn:{name}", "name": name, "label_id": None, "substance": name}))
        seen: set[tuple[str, str, tuple[str, ...], str | None]] = set()
        for m in self._medications.values():
            if not m.substances:
                continue
            names = [normalize_name(m.trade_name), *m.substances]
            if not any(q in n for n in names):
                continue
            key = (normalize_name(m.trade_name), m.form, m.substances, m.label_id)
            if key in seen:
                continue
            seen.add(key)
            rank = 1 if q in names else 3 if any(n.startswith(q) for n in names) else 5
            substance = " + ".join(m.substances)
            title = " · ".join(p for p in (m.trade_name, m.form) if p)
            choices.append(
                (
                    rank,
                    normalize_name(title),
                    {
                        "code": f"record:{m.id}",
                        "name": f"{title} → {substance}",
                        "label_id": m.label_id,
                        "substance": substance,
                    },
                )
            )
        choices.sort(key=lambda item: (item[0], item[1]))
        return [item[2] for item in choices[:limit]], len(choices)

    @property
    def version(self) -> str:
        return self._version

    def get(self, label_id: str) -> DrugLabel:
        try:
            return self._by_id[label_id]
        except KeyError:
            raise NotFoundError(f"Неизвестный препарат (ОХЛП): {label_id}") from None

    def groups_of_substance(self, substance: str) -> frozenset[str]:
        key = normalize_name(substance)
        labels = self._by_substance.get(key)
        if labels:
            return frozenset().union(*(label.groups for label in labels))
        return self._groups.get(key, frozenset())

    def labels(self) -> Sequence[DrugLabel]:
        """All labels in the snapshot."""
        return tuple(self._by_id.values())

    def by_substance(self, substance: str) -> Sequence[DrugLabel]:
        """All single-substance labels for an МНН (normalised match)."""
        return tuple(self._by_substance.get(normalize_name(substance), ()))

    def browse(self, *, offset: int = 0, limit: int = 20) -> Page[DrugLabel]:
        """Every label alphabetically by МНН, then trade name; labels without an МНН last."""
        return self.search("", offset=offset, limit=limit)

    def forms(self) -> tuple[str, ...]:
        """Distinct dosage forms as recorded in the catalog; empty means unspecified."""
        return tuple(sorted({normalize_name(label.form) for label in self._by_id.values()}))

    def search(
        self,
        query: str,
        *,
        substance: str = "",
        trade_name: str = "",
        forms: Sequence[str] | None = None,
        offset: int = 0,
        limit: int = 20,
    ) -> Page[DrugLabel]:
        """Labels whose trade name or substance contains ``query`` (normalised).

        Ranking: exact substance/trade-name match, then prefix, then substring; ties by trade name.
        Name filters intersect; forms are alternatives. None selects all forms, an empty list none.
        """
        q = normalize_name(query)
        substance = normalize_name(substance)
        trade_name = normalize_name(trade_name)
        selected_forms = None if forms is None else {normalize_name(form) for form in forms}
        ranked: list[tuple[int, str, DrugLabel]] = []
        for label in self._by_id.values():
            if selected_forms is not None and normalize_name(label.form) not in selected_forms:
                continue
            if substance and not any(substance in s for s in label.substances):
                continue
            if trade_name and trade_name not in normalize_name(label.trade_name):
                continue
            names = [normalize_name(label.trade_name), *label.substances, normalize_name(label.atc or "")]
            if any(n == q for n in names):
                rank = 0
            elif any(n.startswith(q) for n in names):
                rank = 1
            elif any(q in n for n in names):
                rank = 2
            else:
                continue
            ranked.append((rank, normalize_name(label.display_name), label))
        ranked.sort(key=lambda t: (t[0], t[1]))
        if not q:
            ranked.sort(key=lambda t: (not t[2].substances, t[2].substances, t[1]))
        items = tuple(t[2] for t in ranked[offset : offset + limit])
        return Page(items=items, total=len(ranked), offset=offset, limit=limit)

    def vocabulary(self) -> Vocabulary:
        """Names a doctor can pick for the profile lists: every МНН, every trade name with its МНН, and
        every flagged excipient. The checks match by МНН and excipient, so picks from here always match."""
        trades: dict[str, set[str]] = {}
        for label in self._by_id.values():
            if label.trade_name and label.substances:
                trades.setdefault(label.trade_name.strip(), set()).update(label.substances)
        return Vocabulary(
            substances=tuple(sorted({s for label in self._by_id.values() for s in label.substances})),
            trade_names=tuple(
                (name, tuple(sorted(subs))) for name, subs in sorted(trades.items(), key=lambda t: normalize_name(t[0]))
            ),
            excipients=tuple(sorted({e for label in self._by_id.values() for e in label.excipients.value})),
        )

    def __len__(self) -> int:
        return len(self._by_id)


_MAX_START = re.compile(r"максимальн\w*", re.IGNORECASE)
_CLAUSE_END = re.compile(r"[;]|\.(?=\s|$)")
_DOSE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(мг|г)(?![а-яa-z]*\s*/\s*(?:кг|м))", re.IGNORECASE)
_PER_KG = re.compile(r"\d+(?:[.,]\d+)?\s*(?:мг|г)\s*/\s*(?:кг|м)", re.IGNORECASE)
_SINGLE_DOSE = re.compile(r"разов", re.IGNORECASE)
_DAILY_AFTER = re.compile(r"суточн\w*[^0-9]{0,30}?(\d+(?:[.,]\d+)?)\s*(мг|г)(?![а-я]*\s*/)", re.IGNORECASE)
_POPULATION = re.compile(r"дет|подрост|пожил|взросл|старше|младше|почечн|печеночн|печёночн", re.IGNORECASE)


def _mg(number: str, unit: str) -> Decimal:
    value = Decimal(number.replace(",", "."))
    return value * 1000 if unit.lower() == "г" else value


def parse_max_daily_dose_mg(text: str | None) -> Decimal | None:
    """Parse an unambiguous maximum daily dose into milligrams.

    Looks at every clause that starts with «максимальн…» (up to «;» or the end of the sentence).
    ``"Максимальная рекомендованная суточная доза – 750 мг"`` → 750; grams are converted.
    Returns None when nothing is found, the dose is per kg / per m², single and daily maxima are
    mixed without a clear daily value, the clause names a population (children, elderly, renal ...),
    or different maxima are mentioned anywhere.
    """
    if not text:
        return None
    values: set[Decimal] = set()
    for match in _MAX_START.finditer(text):
        rest = text[match.start() :]
        end = _CLAUSE_END.search(rest)
        clause = rest[: end.start()] if end else rest
        if _PER_KG.search(clause) and not _DOSE.search(_PER_KG.sub("", clause)):
            continue
        clause = _PER_KG.sub("", clause)
        if _SINGLE_DOSE.search(clause):
            daily = _DAILY_AFTER.search(clause)
            if daily:
                values.add(_mg(*daily.groups()))
            continue
        numbers = {_mg(n, u) for n, u in _DOSE.findall(clause)}
        if not numbers:
            continue
        if _POPULATION.search(clause):
            return None  # a maximum for a specific population is not a general limit
        values |= numbers
    return values.pop() if len(values) == 1 else None


@dataclass(frozen=True, slots=True)
class ParsedRow:
    """Result of parsing one CSV row.

    ``label`` is None for out-of-scope rows, which still feed the substance → groups dictionary.
    """

    label: DrugLabel | None
    substance: str
    groups: frozenset[str]
    warnings: tuple[str, ...]


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return None if value.lower() in _NO_DATA else value


def _split(value: str | None) -> tuple[str, ...]:
    value = (value or "").strip()
    if value.lower() in _NO_DATA or value.lower() == "нет":
        return ()
    return tuple(part.strip() for part in value.split(";") if part.strip())


def _status(row: Mapping[str, str], column: str, short: Mapping[str, Status] | None = None) -> Fact[Status]:
    raw = (row.get(column) or "").strip().lower()
    value = (short or {}).get(raw) or _STATUS_WORDS.get(raw, Status.NO_DATA)
    return Fact(value, _text(row.get(f"{column}_quote")), _text(row.get(f"{column}_sec")))


def label_id_for(source_file: str) -> str:
    """Stable label id: first 16 hex chars of sha1(file name)."""
    return hashlib.sha1(source_file.encode("utf-8")).hexdigest()[:16]


def membership_groups(
    substances: Iterable[str], atc: str | None, membership: Sequence[tuple[str, frozenset[str], tuple[str, ...]]]
) -> frozenset[str]:
    """Groups a drug belongs to by explicit membership (substance list or ATC prefix)."""
    names = set(substances)
    return frozenset(
        group
        for group, members, prefixes in membership
        if names & members or (atc and any(atc.startswith(p) for p in prefixes))
    )


def parse_row(
    row: Mapping[str, str],
    *,
    drug_groups: frozenset[str],
    patient_level_groups: frozenset[str],
    membership: Sequence[tuple[str, frozenset[str], tuple[str, ...]]] = (),
) -> ParsedRow:
    """Parse one CSV row.

    ``drug_groups`` is the interaction-group vocabulary (from ``checks.yaml``);
    ``patient_level_groups`` come from the check rules (e.g. «алкоголь»). Groups in 4.5 fields that
    are in neither set are reported in ``warnings`` and dropped, never silently kept.
    """
    source_file = (row.get("file") or "").strip()
    substances = tuple(normalize_name(s) for s in _split(row.get("mnn")))
    substance = substances[0] if len(substances) == 1 else ""
    groups = frozenset(_split(row.get("int_groups"))) | membership_groups(substances, _text(row.get("atx")), membership)
    if (row.get("in_scope") or "").strip() != "да":
        return ParsedRow(None, substance, groups, ())

    warnings: list[str] = []

    def interaction_groups(column: str) -> tuple[frozenset[str], frozenset[str]]:
        drug, patient = set(), set()
        for group in _split(row.get(column)):
            if group in drug_groups:
                drug.add(group)
            elif group in patient_level_groups:
                patient.add(group)
            else:
                warnings.append(f"{source_file}: неизвестная группа взаимодействия {group!r} в {column}")
        return frozenset(drug), frozenset(patient)

    contra_drug, contra_patient = interaction_groups("int_contra")
    caution_drug, caution_patient = interaction_groups("int_caution")
    min_age_raw = _text(row.get("min_age"))
    try:
        min_age = int(float(min_age_raw)) if min_age_raw else None
    except ValueError:
        warnings.append(f"{source_file}: нераспознанный минимальный возраст {min_age_raw!r}")
        min_age = None
    max_text = _text(row.get("dose_max_quote")) or _text(row.get("dose_max"))
    pregnancy_short = {"contra": Status.CONTRAINDICATED, "caution": Status.CAUTION}
    form = ""
    parts = source_file.removesuffix(".pdf.md").split("_")
    if len(parts) >= 3:
        form = parts[2].split(" [")[0].strip()
        if form.lower().startswith("лекформа неизвестна"):
            form = ""

    label = DrugLabel(
        id=label_id_for(source_file),
        trade_name=(_text(row.get("trade")) or (parts[1].replace("®", "") if len(parts) >= 2 else "")).strip(),
        form=form,
        atc=_text(row.get("atx")),
        substances=substances,
        groups=groups,
        indications=Fact(_text(row.get("indications")), _text(row.get("indications_quote")), "4.1"),
        min_age=Fact(min_age, _text(row.get("min_age_quote")), _text(row.get("min_age_sec"))),
        pregnancy=_status(row, "preg", pregnancy_short),
        lactation=_status(row, "lact", pregnancy_short),
        elderly=_status(row, "elderly"),
        renal=_status(row, "renal"),
        renal_gfr_threshold=Fact(
            _text(row.get("renal_gfr")), _text(row.get("renal_gfr_quote")), _text(row.get("renal_gfr_sec"))
        ),
        hepatic=_status(row, "hepatic"),
        hepatic_child_pugh=Fact(
            _text(row.get("hepatic_childpugh")),
            _text(row.get("hepatic_childpugh_quote")),
            _text(row.get("hepatic_childpugh_sec")),
        ),
        driving=_status(row, "driving"),
        contraindicated_icd=Fact(
            _split(row.get("contra_icd")),
            _text(row.get("contra_conditions_quote")),
            _text(row.get("contra_conditions_sec")),
        ),
        caution_conditions=Fact(
            _text(row.get("caution_conditions")),
            _text(row.get("caution_conditions_quote")),
            _text(row.get("caution_conditions_sec")),
        ),
        contraindicated_with=Fact(contra_drug, _text(row.get("int_contra_quote")), _text(row.get("int_contra_sec"))),
        caution_with=Fact(caution_drug, _text(row.get("int_caution_quote")), _text(row.get("int_caution_sec"))),
        patient_level_contraindicated=Fact(
            contra_patient, _text(row.get("int_contra_quote")), _text(row.get("int_contra_sec"))
        ),
        patient_level_caution=Fact(
            caution_patient - contra_patient, _text(row.get("int_caution_quote")), _text(row.get("int_caution_sec"))
        ),
        named_interactions=Fact(
            tuple(n for n in (normalize_name(x) for x in _split(row.get("int_named"))) if n not in substances),
            _text(row.get("int_named_quote")),
            _text(row.get("int_named_sec")),
        ),
        excipients=Fact(
            tuple(normalize_name(x) for x in _split(row.get("excip_flags"))),
            _text(row.get("excip_flags_quote")),
            _text(row.get("excip_flags_sec")),
        ),
        dose=DoseInfo(
            start_text=Fact(
                _text(row.get("dose_start")), _text(row.get("dose_start_quote")), _text(row.get("dose_start_sec"))
            ),
            max_text=Fact(max_text, _text(row.get("dose_max_quote")), _text(row.get("dose_max_sec"))),
            frequency_text=Fact(
                _text(row.get("dose_freq")), _text(row.get("dose_freq_quote")), _text(row.get("dose_freq_sec"))
            ),
            max_daily_mg=None if len(substances) != 1 else parse_max_daily_dose_mg(max_text),
        ),
        antidepressant_profile=_text(row.get("ad_profile")),
        needs_check=(row.get("needs_check") or "").strip() == "да",
        needs_check_reason=_text(row.get("needs_check_reason")),
        source_file=source_file,
    )
    return ParsedRow(label, substance, groups, tuple(warnings))


REQUIRED_COLUMNS = frozenset(
    {"file", "trade", "mnn", "in_scope", "int_groups", "preg", "lact", "contra_icd", "int_contra", "int_caution"}
)


def load_catalog_from_csv(
    path: Path,
    *,
    drug_groups: frozenset[str],
    patient_level_groups: frozenset[str],
    membership: Sequence[tuple[str, frozenset[str], tuple[str, ...]]] = (),
    version: str | None = None,
    substance_aliases: Mapping[str, str] | None = None,
) -> tuple[DrugCatalog, list[str]]:
    """Read ``ohlp_fields_v4.csv`` and build the catalog.

    ``drug_groups`` is the interaction-group vocabulary (``checks.yaml``: ``interaction_groups``); it is
    data, not derived from the file, so a partial CSV can't silently shrink it.
    ``version`` defaults to ``"ohlp:" + first 12 hex chars of the file's sha256``.
    Returns the catalog and all import warnings.

    Raises:
        DefinitionError: required columns are missing or the file can't be read.
    """
    try:
        with path.open(encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            missing = REQUIRED_COLUMNS - set(reader.fieldnames or ())
            if missing:
                raise DefinitionError(f"{path.name}: нет столбцов {sorted(missing)}")
            rows = list(reader)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()[:12]
    except OSError as exc:
        raise DefinitionError(f"Не удалось прочитать {path}: {exc}") from exc

    labels: list[DrugLabel] = []
    medications: list[MedicationName] = []
    groups: dict[str, set[str]] = {}
    warnings: list[str] = []
    for row in rows:
        parsed = parse_row(
            row, drug_groups=drug_groups, patient_level_groups=patient_level_groups, membership=membership
        )
        source_file = (row.get("file") or "").strip()
        parts = source_file.removesuffix(".pdf.md").split("_")
        trade = (_text(row.get("trade")) or (parts[1] if len(parts) > 1 else "")).strip()
        form = parts[2].split(" [")[0].strip() if len(parts) > 2 else ""
        if form.lower().startswith("лекформа неизвестна"):
            form = ""
        aliases = substance_aliases or {}
        names = tuple(
            component.strip()
            for s in _split(row.get("mnn"))
            for component in aliases.get(normalize_name(s), normalize_name(s)).split(" + ")
            if component.strip()
        )
        medications.append(
            MedicationName(
                label_id_for(source_file),
                trade,
                form,
                names,
                parsed.label.id if parsed.label else None,
            )
        )
        if parsed.substance and parsed.groups:
            canonical = aliases.get(parsed.substance, parsed.substance)
            groups.setdefault(canonical, set()).update(parsed.groups)
        if parsed.label is not None:
            labels.append(parsed.label)
        warnings.extend(parsed.warnings)
    for group, members, _ in membership:
        for substance in members:
            groups.setdefault(substance, set()).add(group)
    used = {g for label in labels for g in label.contraindicated_with.value | label.caution_with.value}
    populated = {g for label in labels for g in label.groups} | {g for v in groups.values() for g in v}
    warnings += [
        f"Группа «{g}» используется в 4.5, но в базе нет ни одного её препарата" for g in sorted(used - populated)
    ]
    catalog = DrugCatalog(
        version or f"ohlp:{digest}",
        labels,
        {k: frozenset(v) for k, v in groups.items()},
        medications,
    )
    return catalog, warnings
