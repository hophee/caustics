import csv
from datetime import date, time
from decimal import Decimal
from pathlib import Path

import pytest

from kompas.core.errors import InvalidInputError
from kompas.core.icd import IcdCode
from kompas.modules.cases.domain.profile import Sex, Tristate
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.drugs import DrugCatalog, load_catalog_from_csv, parse_max_daily_dose_mg
from kompas.modules.knowledge.guidelines import GuidelineRow, Guidelines, parse_row_targets
from kompas.modules.therapy.domain.basket import (
    CurrentTherapyItem,
    DailySchedule,
    DoseRegimen,
    Draft,
    DraftItem,
    Intake,
    PrnSchedule,
    Search,
    resulting_therapy,
    to_mg,
)
from kompas.modules.therapy.domain.checks import CheckReport, Severity, Target, TargetPart, check_basket
from kompas.modules.therapy.domain.confirmation import confirm, confirmation_problems
from kompas.modules.therapy.domain.selection import (
    KrStatus,
    provenance,
    select_candidates,
)

from .conftest import make_ctx, make_patient

TRAMADOL = CurrentTherapyItem("rx-1", None, "трамадол", None)


def test_directory_normalizes_tramadol_extraction_variants(tmp_path: Path, knowledge: Knowledge) -> None:
    source = Path(__file__).parent / "fixtures" / "ohlp_sample.csv"
    with source.open(encoding="utf-8-sig", newline="") as stream:
        rows = list(csv.DictReader(stream))
    for row in rows:
        if row["mnn"] == "трамадол":
            row["mnn"] = "– трамадола"
    target = tmp_path / "names.csv"
    with target.open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    catalog, _ = load_catalog_from_csv(
        target,
        drug_groups=frozenset(),
        patient_level_groups=frozenset(),
        substance_aliases=knowledge.guidelines.vocabulary.substance_aliases,
    )
    found, _ = catalog.search_medications("ТРАМАДОЛ АВЕКСИМА")
    assert found[0]["substance"] == "трамадол"
    assert "– трамадола" not in catalog.medication_substances()


def test_full_directory_keeps_external_names_and_combination_interactions(
    knowledge: Knowledge, catalog: DrugCatalog
) -> None:
    found, total = catalog.search_medications("ТРАМАДОЛ АВЕКСИМА")
    assert total == 1 and found[0]["substance"] == "трамадол" and found[0]["label_id"] is None
    combo = CurrentTherapyItem("rx-combo", None, "парацетамол + трамадол", None)
    report = check_basket((combo,), Draft((item(catalog, "a", "сертралин"),)), make_ctx(knowledge))
    assert any("interaction" in code for code in codes(report))


def regimen(mg: int, days: int = 28) -> DoseRegimen:
    return DoseRegimen(DailySchedule((Intake(time(8), Decimal(mg)),)), date(2026, 10, 20), days)


def item(catalog: DrugCatalog, key: str, mnn: str, mg: int = 50) -> DraftItem:
    return DraftItem(key, catalog.by_substance(mnn)[0].id, regimen(mg), Search())


def codes(report: CheckReport) -> set[str]:
    return {w.code for w in report.warnings}


# ------------------------------------------------------------------------------------------ basket


def test_to_mg_and_regimen_maths() -> None:
    assert to_mg(Decimal("0.5"), "г") == Decimal(500)
    with pytest.raises(InvalidInputError):
        to_mg(Decimal(1), "таблетка")
    daily = DoseRegimen(
        DailySchedule((Intake(time(8), Decimal(25)), Intake(time(20), Decimal(50)))), date(2026, 1, 1), 10
    )
    assert daily.max_daily_mg == 75 and daily.end == date(2026, 1, 10)
    prn = DoseRegimen(PrnSchedule(Decimal("0.5"), 3), date(2026, 1, 1), 7)
    assert prn.max_daily_mg == Decimal("1.5")
    assert DoseRegimen(DailySchedule(()), date(2026, 1, 1), 0).problems()  # no intakes, no duration


def test_stopping_current_drug_removes_it(catalog: DrugCatalog) -> None:
    draft = Draft((item(catalog, "a", "сертралин"),), stopped=frozenset({"rx-1"}))
    keys = [t.key for t in resulting_therapy((TRAMADOL,), draft)]
    assert keys == ["a"]
    with pytest.raises(InvalidInputError):
        resulting_therapy((TRAMADOL,), Draft((), stopped=frozenset({"nope"})))


# ------------------------------------------------------------------------------------------ checks


@pytest.mark.parametrize(
    ("lactating", "mnn", "expected_code", "severity"),
    [
        (Tristate.YES, "амитриптилин", "lactation_contraindicated", Severity.CONTRAINDICATED),
        (Tristate.YES, "сертралин", "lactation_caution", Severity.CAUTION),
        (Tristate.YES, "миртазапин", "lactation_no_data", Severity.NO_DATA),
        (Tristate.UNKNOWN, "амитриптилин", "lactation_unknown", Severity.CAUTION),
        (Tristate.NO, "амитриптилин", None, None),
    ],
)
def test_lactation_matrix(
    knowledge: Knowledge,
    catalog: DrugCatalog,
    lactating: Tristate,
    mnn: str,
    expected_code: str | None,
    severity: Severity | None,
) -> None:
    ctx = make_ctx(knowledge, make_patient(lactating=lactating))
    report = check_basket((), Draft((item(catalog, "a", mnn),)), ctx)
    lactation = [w for w in report.warnings if w.code.startswith("lactation")]
    if expected_code is None:
        assert lactation == []
    else:
        assert [(w.code, w.severity) for w in lactation] == [(expected_code, severity)]


def test_pregnancy_not_applicable_for_men_and_outside_age(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    draft = Draft((item(catalog, "a", "амитриптилин"),))
    man = make_ctx(knowledge, make_patient(sex=Sex.MALE, pregnant=Tristate.UNKNOWN, lactating=Tristate.UNKNOWN))
    elderly_woman = make_ctx(knowledge, make_patient(birth_date=date(1950, 1, 1), pregnant=Tristate.UNKNOWN))
    for ctx in (man, elderly_woman):
        assert not any(c.startswith(("pregnancy", "lactation")) for c in codes(check_basket((), draft, ctx)))


def test_interaction_is_one_warning_boxing_both_cards(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    report = check_basket((TRAMADOL,), Draft((item(catalog, "a", "сертралин"),)), make_ctx(knowledge))
    pair = [w for w in report.warnings if w.kind == "interaction"]
    assert len(pair) == 1
    assert {t.item_key for t in pair[0].targets} == {"current:rx-1", "a"}
    assert "трамадол" in pair[0].message
    boxes = report.boxes()
    assert boxes[Target("a", TargetPart.ITEM)] is Severity.CAUTION
    assert boxes[Target("current:rx-1", TargetPart.ITEM)] is Severity.CAUTION


def test_contraindicated_pair_merges_sources_and_takes_worst(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    draft = Draft((item(catalog, "a", "флувоксамин"), item(catalog, "b", "агомелатин")))
    report = check_basket((), draft, make_ctx(knowledge))
    pair = [w for w in report.warnings if w.kind == "interaction"]
    assert len(pair) == 1 and pair[0].severity is Severity.CONTRAINDICATED
    assert len(pair[0].sources) >= 2  # group rule + reviewed override
    assert pair[0].involves_new_item


def test_dose_above_max_boxes_only_dose(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    label = catalog.by_substance("амитриптилин")[0]
    assert label.dose.max_daily_mg == 150
    report = check_basket((), Draft((item(catalog, "a", "амитриптилин", mg=300),)), make_ctx(knowledge))
    dose = [w for w in report.warnings if w.code == "dose_above_max"]
    assert len(dose) == 1 and dose[0].targets == (Target("a", TargetPart.DOSE),)
    assert report.boxes()[Target("a", TargetPart.DOSE)] is Severity.CONTRAINDICATED


def test_unknown_max_dose_is_grey_hover_not_box(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    assert catalog.by_substance("агомелатин")[0].dose.max_daily_mg is None
    report = check_basket((), Draft((item(catalog, "a", "агомелатин", mg=500),)), make_ctx(knowledge))
    entry = next(w for w in report.warnings if w.code == "dose_not_verifiable")
    assert entry.severity is Severity.NO_DATA
    assert Target("a", TargetPart.DOSE) not in report.boxes()


def test_missing_patient_lists_are_reported(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    ctx = make_ctx(knowledge, make_patient(allergies=None, intolerant_excipients=None))
    found = codes(check_basket((), Draft((item(catalog, "a", "миртазапин"),)), ctx))
    assert {"allergies_unknown", "excipients_unknown"} <= found


def test_icd_contraindication_and_alcohol(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    ctx = make_ctx(knowledge, make_patient(diagnoses=(IcdCode.parse("F10.2"),), substance_use=Tristate.YES))
    draft = Draft((item(catalog, "a", "амитриптилин"), item(catalog, "b", "сертралин")))
    report = check_basket((), draft, ctx)
    icd = [w for w in report.warnings if w.code == "icd_contraindicated"]
    assert [t.item_key for w in icd for t in w.targets] == ["a"]  # amitriptyline lists F10 in 4.3
    alcohol = [w for w in report.warnings if w.code == "patient_level_substance_use"]
    assert [t.item_key for w in alcohol for t in w.targets] == ["b"]  # sertraline: alcohol in 4.5


def test_browse_lists_every_label_by_substance(catalog: DrugCatalog) -> None:
    first = catalog.browse(limit=5)
    assert first.total == len(catalog)
    rest = catalog.browse(offset=5, limit=len(catalog))
    keys = [(not lb.substances, lb.substances) for lb in (*first.items, *rest.items)]
    assert keys == sorted(keys)
    assert len({lb.id for lb in (*first.items, *rest.items)}) == len(catalog)


def test_vocabulary_matches_what_checks_compare(catalog: DrugCatalog) -> None:
    vocabulary = catalog.vocabulary()
    assert "сертралин" in vocabulary.substances
    assert set(vocabulary.substances) == {s for lb in catalog.labels() for s in lb.substances}
    assert {s for _, subs in vocabulary.trade_names for s in subs} <= set(vocabulary.substances)
    assert set(vocabulary.excipients) == {e for lb in catalog.labels() for e in lb.excipients.value}
    assert "лактоза" in vocabulary.excipients


def test_fixture_imports_without_warnings(knowledge: Knowledge) -> None:
    assert not [w for w in knowledge.import_warnings if "группа взаимодействия" in w]


def test_group_load_and_duplicate(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    draft = Draft(
        (item(catalog, "a", "сертралин"), item(catalog, "b", "амитриптилин"), item(catalog, "c", "сертралин"))
    )
    found = codes(check_basket((TRAMADOL,), draft, make_ctx(knowledge)))
    assert "group_load:серотонинергические" in found
    assert "duplicate_substance" in found


def test_warning_ids_stable_across_card_keys(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    a = check_basket((), Draft((item(catalog, "x1", "амитриптилин", mg=300),)), make_ctx(knowledge))
    b = check_basket((), Draft((item(catalog, "other", "амитриптилин", mg=300),)), make_ctx(knowledge))
    assert {w.id for w in a.warnings} == {w.id for w in b.warnings}


# ------------------------------------------------------------------------------------------ confirmation


def test_red_needs_justification_then_confirms(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    ctx = make_ctx(knowledge, make_patient(lactating=Tristate.YES))
    draft = Draft((item(catalog, "a", "амитриптилин"),))
    prov = {
        "a": provenance(
            Search(), catalog.get(draft.new_items[0].label_id), IcdCode.parse("F33.2"), knowledge.guidelines
        )
    }
    problems = confirmation_problems((), draft, ctx, {}, frozenset(), prov)
    red = [p for p in problems if p.code == "justification_required"]
    assert len(red) == 1
    too_short = confirmation_problems((), draft, ctx, {red[0].pointer: "ок"}, frozenset(), prov)  # type: ignore[dict-item]
    assert any(p.code == "justification_required" for p in too_short)
    justification = {red[0].pointer: "Грудное вскармливание прекращено"}  # type: ignore[dict-item]
    snapshot = confirm(
        (),
        draft,
        ctx,
        justification,
        frozenset(),
        prov,
        doctor_id="d1",
        now=__import__("datetime").datetime(2026, 10, 19),
    )
    assert snapshot.knowledge_version == knowledge.version
    assert dict(snapshot.justifications) == justification
    assert snapshot.items[0].provenance is not None


def test_provenance_required_and_nothing_to_confirm(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    ctx = make_ctx(knowledge)
    draft = Draft((item(catalog, "a", "сертралин"),))
    assert any(p.code == "provenance_missing" for p in confirmation_problems((), draft, ctx, {}, frozenset(), {}))
    assert any(p.code == "nothing_to_confirm" for p in confirmation_problems((), Draft(()), ctx, {}, frozenset(), {}))


def test_previously_justified_red_on_continued_drug_needs_no_new_justification(
    knowledge: Knowledge, catalog: DrugCatalog
) -> None:
    ctx = make_ctx(knowledge, make_patient(lactating=Tristate.YES))
    current = (CurrentTherapyItem("rx-2", catalog.by_substance("амитриптилин")[0].id, None, regimen(50)),)
    report = check_basket(current, Draft((), stopped=frozenset()), ctx)
    red = report.requiring_justification(frozenset())
    assert red  # new red on continued therapy (e.g. lactation started) needs a decision
    assert report.requiring_justification(frozenset(w.id for w in red)) == ()


# ------------------------------------------------------------------------------------------ guidelines & selection


def test_max_dose_parser() -> None:
    assert parse_max_daily_dose_mg("Максимальная суточная доза – 200 мг.") == 200
    assert parse_max_daily_dose_mg("максимальная доза 3 г") == 3000
    assert parse_max_daily_dose_mg("взрослым максимальная доза 200 мг; детям максимальная доза 50 мг") is None
    assert parse_max_daily_dose_mg("нет данных") is None


def test_row_targets_parsing(knowledge: Knowledge) -> None:
    g = knowledge.guidelines
    targets, text_only, unparsed = parse_row_targets(
        "СИОЗС (сертралин, пароксетин); лития соли, рисперидон",
        "рисперидон",
        "A / 1; B / 2",
        g.classes,
        g.window_rules,
        g.vocabulary,
    )
    names = {(t.mnn, t.off_label) for t in targets if t.kind == "substance"}  # type: ignore[union-attr]
    assert names == {("сертралин", False), ("пароксетин", False), ("лития карбонат", False), ("рисперидон", True)}
    assert not text_only and unparsed == ()
    _, text_only, unparsed = parse_row_targets(
        "увеличить дозу до максимальной", "", "", g.classes, g.window_rules, g.vocabulary
    )
    assert text_only and unparsed == ("увеличить дозу до максимальной",)


def test_selection_lists_every_row_of_the_diagnosis(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    selection = select_candidates(IcdCode.parse("F33.2"), knowledge.guidelines, catalog, make_patient())
    substances = {c.label.substances[0] for c in selection.candidates}
    assert {"амитриптилин", "миртазапин"} <= substances  # fixture contains these
    assert all(c.row.code == "F33.2" for c in selection.candidates)
    rows = [r for r in knowledge.guidelines.rows if r.code == "F33.2"]
    assert selection.rows == tuple(rows)  # no stage / line filter, conditional rows included (D-122)
    assert len({r.stage for r in selection.rows}) > 1 and any(r.condition_kind == "mixed" for r in selection.rows)
    mixed = [r.rule for r in selection.restrictions_to_clarify if "смешанн" in r.condition.lower()]
    assert mixed and not any("смешанн" in r.condition.lower() for r in selection.restrictions)


def test_unparsed_fragment_is_advice_and_unverified(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    """Reviewer condition: unparsed КР text must surface as advice, and a drug that may sit in it is
    UNVERIFIED, never NOT_MENTIONED."""
    base = knowledge.guidelines
    row = GuidelineRow(
        id="kr-test",
        code="F41.1",
        stage="1-я линия",
        condition="",
        condition_kind="none",
        scheme="Монотерапия",
        window="Анксиолитик",
        substances_text="неизвестный препарат X",
        targets=(),
        text_only=False,
        unparsed=("неизвестный препарат X",),
        evidence="C / 5",
        source="КР test",
        quote="«...»",
    )
    g = Guidelines(
        version="test",
        diagnoses={"F41.1": "ГТР"},
        rows=(row,),
        classes=base.classes,
        window_rules=base.window_rules,
        restrictions=(),
    )
    selection = select_candidates(IcdCode.parse("F41.1"), g, catalog, make_patient())
    assert [a.text for a in selection.advice] == ["неизвестный препарат X"]
    prov = provenance(Search(), catalog.by_substance("диазепам")[0], IcdCode.parse("F41.1"), g)
    assert prov.kr_status is KrStatus.UNVERIFIED and prov.row is not None and prov.row.id == "kr-test"
