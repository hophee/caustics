from dataclasses import replace

from kompas.modules.knowledge.drugs import DrugCatalog, parse_row


def search_catalog() -> DrugCatalog:
    parsed = parse_row(
        {"file": "sample.pdf.md", "in_scope": "да"},
        drug_groups=frozenset(), patient_level_groups=frozenset(),
    )
    assert parsed.label is not None
    labels = [
        replace(parsed.label, id="a", trade_name="Золофт", substances=("сертралин",), form="Таблетки", atc="N06AB06"),
        replace(parsed.label, id="b", trade_name="Золофт", substances=("сертралин",), form="Капли"),
        replace(parsed.label, id="c", trade_name="Сертралин", substances=("другое вещество",), form="Таблетки"),
        replace(parsed.label, id="d", trade_name="Комбинация", substances=("сертралин", "другое вещество"), form=""),
    ]
    return DrugCatalog("test", labels, {})


def test_general_and_separate_name_searches() -> None:
    catalog = search_catalog()
    assert catalog.search("  СЕРТРАЛИН® ").total == 4
    assert {label.id for label in catalog.search("", substance="СЕРТР").items} == {"a", "b", "d"}
    assert [label.id for label in catalog.search("", trade_name="сертр").items] == ["c"]
    assert catalog.search("", substance="сертралин", trade_name="сертралин").total == 0
    assert catalog.search("несуществующий").total == 0


def test_forms_combine_with_search_before_pagination() -> None:
    catalog = search_catalog()
    page = catalog.search("золофт", substance="серт", forms=[" ТАБЛЕТКИ ", "капли"], offset=1, limit=1)
    assert page.total == 2
    assert len(page.items) == 1
    assert page.offset == 1 and page.limit == 1
    assert [label.id for label in catalog.search("золофт", forms=["капли"]).items] == ["b"]
    assert catalog.search("", forms=["капли"], offset=1).items == ()
    assert catalog.search("", forms=[]).total == 0
    assert catalog.search("", forms=["unknown"]).total == 0
    assert [label.id for label in catalog.search("", forms=[""]).items] == ["d"]
    assert catalog.forms() == ("", "капли", "таблетки")
    assert catalog.browse().total == 4
    assert catalog.browse().items[0].id == "c"


def test_atc_search_combines_with_name_and_form_filters() -> None:
    catalog = search_catalog()
    page = catalog.search("n06", substance="серт", trade_name="зол", forms=["таблетки"])
    assert [label.id for label in page.items] == ["a"]
    assert catalog.search("N06AB06", forms=["капли"]).total == 0
