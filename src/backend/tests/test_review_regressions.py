"""Regression tests for the findings of the first code review (ids refer to that review)."""

from datetime import date, time
from decimal import Decimal

import pytest

from kompas.core.errors import DefinitionError, InvalidInputError
from kompas.core.icd import IcdCode
from kompas.modules.assessments.domain.scoring import item_critical_hits, score
from kompas.modules.cases.domain.profile import SuicideRisk, Tristate
from kompas.modules.knowledge.bundle import Knowledge
from kompas.modules.knowledge.drugs import DrugCatalog, parse_max_daily_dose_mg
from kompas.modules.knowledge.scales import parse_scale
from kompas.modules.monitoring.domain.freshness import diary_freshness
from kompas.modules.monitoring.domain.text_model import KeywordModel, TriageOption
from kompas.modules.therapy.domain.basket import (
    CurrentTherapyItem,
    DailySchedule,
    DoseRegimen,
    Draft,
    DraftItem,
    Intake,
    KrSuggestion,
    Search,
)
from kompas.modules.therapy.domain.checks import Severity, Target, TargetPart, check_basket
from kompas.modules.therapy.domain.selection import KrStatus, provenance, select_candidates

from .conftest import make_ctx, make_patient
from .test_scales_and_scoring import _scale


def regimen(mg: int) -> DoseRegimen:
    return DoseRegimen(DailySchedule((Intake(time(8), Decimal(mg)),)), date(2026, 10, 20), 28)


def new(catalog: DrugCatalog, key: str, mnn: str, mg: int = 50) -> DraftItem:
    return DraftItem(key, catalog.by_substance(mnn)[0].id, regimen(mg), Search())


def test_c1_ssri_with_mao_inhibitor_is_red(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    assert "ИМАО" in catalog.groups_of_substance("моклобемид")  # not in the data at all: from membership
    current = (CurrentTherapyItem("rx-maoi", None, "моклобемид", None),)
    report = check_basket(current, Draft((new(catalog, "a", "сертралин"),)), make_ctx(knowledge))
    pair = next(w for w in report.warnings if w.kind == "interaction")
    assert pair.severity is Severity.CONTRAINDICATED and pair.code == "interaction_group_contra"


def test_c2_same_substance_doses_are_summed(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    sertraline = catalog.by_substance("сертралин")[0]
    assert sertraline.dose.max_daily_mg == 200
    current = (CurrentTherapyItem("rx-s", sertraline.id, None, regimen(150)),)
    report = check_basket(current, Draft((new(catalog, "a", "сертралин", mg=150),)), make_ctx(knowledge))
    dose = [w for w in report.warnings if w.code == "dose_above_max"]
    assert len(dose) == 1 and dose[0].severity is Severity.CONTRAINDICATED
    assert {t for t in dose[0].targets} == {Target("current:rx-s", TargetPart.DOSE), Target("a", TargetPart.DOSE)}
    assert not [w for w in report.warnings if w.kind == "interaction"]  # m1: no "Сертралин + Сертралин"


@pytest.mark.parametrize("birth", [date(1960, 1, 1), date(2015, 1, 1)])
def test_c3_recorded_pregnancy_always_counts(knowledge: Knowledge, catalog: DrugCatalog, birth: date) -> None:
    ctx = make_ctx(knowledge, make_patient(birth_date=birth, pregnant=Tristate.YES))
    report = check_basket((), Draft((new(catalog, "a", "амитриптилин"),)), ctx)
    assert "pregnancy_contraindicated" in {w.code for w in report.warnings}


def test_c4_alcohol_contraindication_is_red(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    # agomelatine label: «алкоголь» only as caution; build expectations from the data itself
    label = catalog.by_substance("сертралин")[0]
    expected = Severity.CONTRAINDICATED if "алкоголь" in label.patient_level_contraindicated.value else Severity.CAUTION
    ctx = make_ctx(knowledge, make_patient(substance_use=Tristate.YES))
    report = check_basket((), Draft((new(catalog, "a", "сертралин"),)), ctx)
    alcohol = next(w for w in report.warnings if w.code == "patient_level_substance_use")
    assert alcohol.severity is expected


def test_c5_escalated_warning_needs_new_justification(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    current = (CurrentTherapyItem("rx-a", catalog.by_substance("амитриптилин")[0].id, None, regimen(50)),)
    before = check_basket(current, Draft(()), make_ctx(knowledge, make_patient(lactating=Tristate.NO)))
    after = check_basket(current, Draft(()), make_ctx(knowledge, make_patient(lactating=Tristate.YES)))
    # only red-and-justified ids count as accepted, so the new red lactation warning needs a decision
    justified_before = frozenset(w.id for w in before.requiring_justification(frozenset()))
    assert after.requiring_justification(justified_before)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Максимальная рекомендованная суточная доза – 750 мг", Decimal(750)),
        ("дозу можно увеличить до максимальной – 40 мг в сутки", Decimal(40)),
        ("до максимальной суточной дозы 3 мг/кг", None),
        ("Максимальные дозы: разовая – 300 мг, суточная 800 мг", Decimal(800)),
        ("Максимальная доза для взрослых 200 мг, для детей 100 мг", None),
        ("Максимальная суточная доза 4 мг, у пожилых 2 мг", None),
    ],
)
def test_m1_max_dose_parser(text: str, expected: Decimal | None) -> None:
    assert parse_max_daily_dose_mg(text) == expected


def test_s1_critical_answer_survives_invalid_response(knowledge: Knowledge) -> None:
    d = knowledge.scales["PHQ-9"]
    partial = {9: 3, 2: 1}  # item 1 etc. missing
    with pytest.raises(InvalidInputError):
        score(d, partial)
    assert [h.item for h in item_critical_hits(d, partial)] == [9]


def test_s2_validation_rejects_unreachable_critical() -> None:
    with pytest.raises(DefinitionError):
        parse_scale(_scale(critical=[{"item": 1, "min_value": 4}]))


def test_g1_foreign_row_is_not_trusted(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    foreign = next(r for r in knowledge.guidelines.rows if r.code == "F43.1")
    diazepam = catalog.by_substance("диазепам")[0]
    prov = provenance(KrSuggestion(foreign.id), diazepam, IcdCode.parse("F33.2"), knowledge.guidelines)
    assert prov.row is None or prov.row.code == "F33.2"


def test_g2_window_rows_give_their_own_provenance(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    window_row = next(
        r
        for r in knowledge.guidelines.rows
        if any(t.kind == "window" and t.window == "Антидепрессант" for t in r.targets)  # type: ignore[union-attr]
    )
    label = next(lbl for lbl in catalog.labels() if (lbl.atc or "").startswith("N06A"))
    prov = provenance(KrSuggestion(window_row.id), label, IcdCode.parse(window_row.code), knowledge.guidelines)
    assert prov.kr_status is KrStatus.RECOMMENDED and prov.row is not None and prov.row.id == window_row.id


def test_g3_restrictions_fire(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    g = knowledge.guidelines

    def rules_for(code: str, **patient: object) -> set[str]:
        sel = select_candidates(IcdCode.parse(code), g, catalog, make_patient(**patient))
        return {r.rule for r in sel.restrictions}

    f410 = rules_for("F41.0")
    assert any("Буспирон" in r for r in f410)  # «Любой этап»
    high = rules_for("F33.2", suicide_risk=SuicideRisk.HIGH)
    assert any("стационаре" in r for r in high)
    low = rules_for("F33.2", suicide_risk=SuicideRisk.LOW)
    assert not any("стационаре" in r for r in low)


@pytest.mark.parametrize(
    "text",
    [
        "Хочу умереть",
        "Жить не хочу",
        "Не хочется жить",
        "Думаю повеситься",
        "Наглоталась таблеток вчера",
        "Хочу уснуть и не проснуться",
    ],
)
def test_t1_keyword_safety_net(knowledge: Knowledge, text: str) -> None:
    assert KeywordModel(knowledge.triage_rules.keywords).classify(text).top()[0] is TriageOption.URGENT


def test_t2_freshness_without_data_is_not_complete() -> None:
    f = diary_freshness([], date(2026, 10, 19), window_days=7, since=date(2026, 10, 25))
    assert not f.complete


# ------------------------------------------------------------------------------------------ round 2


def test_n1_justified_yellow_does_not_cover_later_red(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    from datetime import datetime

    from kompas.modules.therapy.domain.confirmation import confirm

    ctx = make_ctx(knowledge, make_patient(lactating=Tristate.NO))
    draft = Draft((new(catalog, "a", "сертралин"), new(catalog, "b", "амитриптилин")))
    report = check_basket((), draft, ctx)
    yellow_pair = next(w for w in report.warnings if w.kind == "interaction")
    assert yellow_pair.severity is Severity.CAUTION
    prov = {
        i.key: provenance(i.origin, catalog.get(i.label_id), IcdCode.parse("F33.2"), knowledge.guidelines)
        for i in draft.new_items
    }
    snapshot = confirm(
        (),
        draft,
        ctx,
        {yellow_pair.id: "пишу на всякий случай"},
        frozenset(),
        prov,
        doctor_id="d",
        now=datetime(2026, 10, 19),
    )
    assert yellow_pair.id not in snapshot.justified_warning_ids
    assert snapshot.justifications == ()


def test_n2_unknown_facts_ask_to_clarify(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    sel = select_candidates(
        IcdCode.parse("F33.2"),
        knowledge.guidelines,
        catalog,
        make_patient(suicide_risk=SuicideRisk.UNKNOWN),
    )
    assert any("стационаре" in r.rule for r in sel.restrictions_to_clarify)
    assert not any("стационаре" in r.rule for r in sel.restrictions)


def test_n3_population_specific_maximum_is_not_general() -> None:
    assert parse_max_daily_dose_mg("Максимальная суточная доза у пожилых пациентов – 20 мг") is None


def test_n4_cssrs_behaviour_alerts_on_partial_answers(knowledge: Knowledge) -> None:
    assert [h.item for h in item_critical_hits(knowledge.scales["C-SSRS"], {"attempt": 1})] == ["attempt"]


def test_n5_disabled_rule_emits_nothing(knowledge: Knowledge, catalog: DrugCatalog) -> None:
    from kompas.modules.knowledge.check_rules import Outcome

    rules = knowledge.check_rules.model_copy(
        update={"severities": {**knowledge.check_rules.severities, "allergy": Outcome.NONE}}
    )
    ctx = make_ctx(knowledge, make_patient(allergies=("сертралин",)))
    ctx = type(ctx)(ctx.knowledge_version, ctx.patient, ctx.drugs, rules, ctx.overrides, ctx.today)
    report = check_basket((), Draft((new(catalog, "a", "сертралин"),)), ctx)
    assert "allergy" not in {w.code for w in report.warnings}


@pytest.mark.parametrize(
    "text", ["Попрощалась с мамой", "Повесилась бы", "Спрыгну с крыши", "Хочу всё закончить", "Устала жить"]
)
def test_t1_more_safety_phrases(knowledge: Knowledge, text: str) -> None:
    assert KeywordModel(knowledge.triage_rules.keywords).classify(text).top()[0] is TriageOption.URGENT
