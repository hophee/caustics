"""Command-line interface for trying the domain logic without a server.

Examples::

    kompas scales list
    kompas scales validate
    kompas scales score PHQ-9 --answers 2,2,3,2,2,2,2,3,1
    kompas drugs search сертралин
    kompas select F33.2
    kompas basket check examples/basket_anna.json
    kompas basket confirm examples/basket_anna.json --justify <warning-id>="текст обоснования"
    kompas triage text "Плохо сплю вторую неделю"
    kompas triage entry examples/diary_entry.json

Global options: ``--knowledge-dir`` (default: env ``KOMPAS_KNOWLEDGE_DIR`` or ``knowledge_data``
next to the package), ``--ohlp`` (default: env ``KOMPAS_OHLP_CSV``), ``--json``.
Every command prints a readable report; ``--json`` prints machine-readable output instead.
"""

import argparse
import contextlib
import json
import os
import sys
from collections.abc import Awaitable, Callable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from kompas.core.errors import KompasError
from kompas.core.icd import IcdCode
from kompas.core.jsonable import to_jsonable
from kompas.core.security import Role
from kompas.json_input import parse_current, parse_diary_entry, parse_draft, parse_patient
from kompas.modules.assessments.domain.scoring import item_critical_hits, score
from kompas.modules.cases.domain.profile import PatientProfile, Sex, SuicideRisk, Tristate
from kompas.modules.knowledge.bundle import Knowledge, load_knowledge
from kompas.modules.knowledge.drugs import DrugCatalog
from kompas.modules.monitoring.domain.text_model import KeywordModel
from kompas.modules.monitoring.domain.triage import (
    PatientContext,
    combine,
    evaluate_entry,
    evaluate_trend,
    model_reason,
)
from kompas.modules.therapy.domain.checks import (
    CheckContext,
    CheckReport,
    Severity,
    Source,
    Target,
    TargetPart,
    check_basket,
)
from kompas.modules.therapy.domain.confirmation import confirm, confirmation_problems
from kompas.modules.therapy.domain.selection import provenance, select_candidates

PACKAGE_KNOWLEDGE_DIR = Path(__file__).resolve().parents[2] / "knowledge_data"
SEVERITY_LABEL = {
    Severity.CONTRAINDICATED: "КРАСНАЯ рамка",
    Severity.CAUTION: "жёлтая рамка",
    Severity.NO_DATA: "серое (только подсказка)",
}


# ------------------------------------------------------------------------------------------- helpers


def _print_json(value: Any) -> None:
    print(json.dumps(to_jsonable(value), ensure_ascii=False, indent=2))


def _knowledge(args: argparse.Namespace, *, need_drugs: bool = False) -> Knowledge:
    directory = Path(args.knowledge_dir or os.environ.get("KOMPAS_KNOWLEDGE_DIR") or PACKAGE_KNOWLEDGE_DIR)
    ohlp = args.ohlp or os.environ.get("KOMPAS_OHLP_CSV")
    if need_drugs and not ohlp:
        raise SystemExit("Нужна база ОХЛП: укажите --ohlp путь/к/ohlp_fields_v4.csv или переменную KOMPAS_OHLP_CSV")
    return load_knowledge(directory, Path(ohlp) if ohlp else None)


def _drugs(knowledge: Knowledge) -> DrugCatalog:
    assert knowledge.drugs is not None
    return knowledge.drugs


def _read_json(path: str) -> dict[str, Any]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"Не удалось прочитать {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise SystemExit(f"{path}: ожидается JSON-объект")
    return data


def _wrap(text: str, indent: str = "      ") -> str:
    words, lines, line = text.split(), [], ""
    for word in words:
        if len(line) + len(word) + 1 > 100 - len(indent):
            lines.append(line)
            line = word
        else:
            line = f"{line} {word}".strip()
    lines.append(line)
    return ("\n" + indent).join(lines)


# ------------------------------------------------------------------------------------------- scales


def cmd_scales_list(args: argparse.Namespace) -> int:
    """Print each scale: code, name, what it measures, who fills it, number of items."""
    knowledge = _knowledge(args)
    if args.json:
        _print_json([{"code": s.code, "name": s.name, "items": len(s.items)} for s in knowledge.scales.values()])
        return 0
    print(f"{'Шкала':10} {'Кто заполняет':10} {'Пункты':>6}  Что измеряет · Название")
    for s in sorted(knowledge.scales.values(), key=lambda s: s.code):
        print(f"{s.code:10} {s.filled_by:10} {len(s.items):>6}  {s.measures} · {s.name}")
    return 0


def cmd_scales_validate(args: argparse.Namespace) -> int:
    """Load all knowledge; print problems; exit code 1 if anything is invalid."""
    knowledge = _knowledge(args)  # raises with details on problems (printed by main)
    print(f"База знаний корректна. Версия {knowledge.version}.")
    print(
        f"  шкал: {len(knowledge.scales)}, жалоб: {len(knowledge.complaints.codes())}, "
        f"строк КР: {len(knowledge.guidelines.rows)}"
    )
    if knowledge.drugs is not None:
        print(f"  препаратов (ОХЛП): {len(knowledge.drugs)}")
    if knowledge.import_warnings:
        print(f"\nПредупреждения импорта ({len(knowledge.import_warnings)}), показываются врачу как текстовые советы:")
        for warning in knowledge.import_warnings[: args.limit]:
            print("  -", warning)
        if len(knowledge.import_warnings) > args.limit:
            print(f"  ... и ещё {len(knowledge.import_warnings) - args.limit} (--limit N)")
    return 0


def cmd_scales_score(args: argparse.Namespace) -> int:
    """Score answers given as a comma list (item order) and print the explanation, what the doctor
    sees and critical hits. The patient sees no result (D-109)."""
    knowledge = _knowledge(args)
    definition = knowledge.scales.get(args.code)
    if definition is None:
        raise SystemExit(f"Нет шкалы {args.code!r}. Доступны: {', '.join(sorted(knowledge.scales))}")
    try:
        values = [int(v) for v in args.answers.split(",") if v.strip()]
    except ValueError:
        raise SystemExit("--answers: ожидаются целые числа через запятую, например 2,2,3,1") from None
    answers = {item.id: v for item, v in zip(definition.items, values, strict=False)}
    if len(values) > len(definition.items):
        answers.update({len(definition.items) + i + 1: v for i, v in enumerate(values[len(definition.items) :])})
    early_hits = item_critical_hits(definition, answers)
    try:
        result = score(definition, answers)
    except KompasError:
        for hit in early_hits:
            print(f"⚠ КРИТИЧЕСКИЙ ОТВЕТ (пункт {hit.item} = {hit.value}) → алерт врачу сразу, даже без полного ответа")
        raise
    if args.json:
        _print_json({"result": result})
        return 0
    print(f"{definition.name}  (версия {definition.version}, источник: {definition.source})\n")
    print("Как посчитано:")
    for step in result.explanation:
        print("  ·", step)
    print("\nВидит врач:")
    if result.total is not None:
        print(f"  итог: {result.total:g}")
    if result.band is not None:
        print(f"  интерпретация: {result.band.label}")
    for sub in result.subscores:
        print(f"  {sub.name}: {sub.value:g} — {sub.band.label if sub.band else "без пороговой интерпретации"}")
    for hit in result.critical_hits:
        where = f"пункт {hit.item} = {hit.value}" if hit.item else f"результат «{hit.band_code}»"
        note = f" — {hit.note}" if hit.note else ""
        print(
            f"  ⚠ КРИТИЧЕСКИЙ ОТВЕТ ({where}){note} → алерт врачу сразу"
            + (" (предложение команды)" if hit.proposal else "")
        )
    print("\nВидит пациент: «Ответы сохранены» (результат не показывается, D-109)")
    return 0


# ------------------------------------------------------------------------------------------- drugs


def cmd_drugs_search(args: argparse.Namespace) -> int:
    """Search the catalog and print matching labels with their key safety facts."""
    catalog = _drugs(_knowledge(args, need_drugs=True))
    page = catalog.search(args.query, offset=args.offset, limit=args.limit)
    if args.json:
        _print_json(page)
        return 0
    print(f"Найдено {page.total}; показаны {page.offset + 1}–{page.offset + len(page.items)}\n")
    for label in page.items:
        dose = label.dose.max_daily_mg
        print(f"{label.display_name}   [id {label.id}]")
        print(
            f"  АТХ {label.atc or '—'} · беременность: {label.pregnancy.value} · лактация: {label.lactation.value}"
            f" · пожилые: {label.elderly.value} · с {label.min_age.value or '?'} лет"
        )
        print(f"  группы: {', '.join(sorted(label.groups)) or '—'}")
        print(f"  нельзя с: {', '.join(sorted(label.contraindicated_with.value)) or '—'}")
        print(f"  макс. суточная доза: {f'{dose:g} мг' if dose else 'не распознана (см. 4.2)'}")
        if label.needs_check:
            print(f"  ⚠ данные требуют сверки: {label.needs_check_reason}")
        print()
    return 0


# ------------------------------------------------------------------------------------------- selection


def cmd_select(args: argparse.Namespace) -> int:
    """Guideline-based suggestions for a diagnosis (every КР row; no stage, D-122)."""
    knowledge = _knowledge(args, need_drugs=True)
    diagnosis = IcdCode.parse(args.diagnosis)
    patient = PatientProfile(
        birth_date=date(1990, 1, 1),
        sex=Sex.FEMALE,
        timezone=ZoneInfo("Europe/Moscow"),
        pregnant=Tristate.YES if args.pregnant else Tristate.UNKNOWN,
        lactating=Tristate.UNKNOWN,
        substance_use=Tristate.UNKNOWN,
        suicide_risk=SuicideRisk(args.suicide_risk),
        diagnoses=(diagnosis,),
        allergies=None,
        intolerant_excipients=None,
        past_intolerance=None,
        past_inefficacy=None,
    )
    selection = select_candidates(diagnosis, knowledge.guidelines, _drugs(knowledge), patient)
    if args.json:
        _print_json(selection)
        return 0
    print(f"{diagnosis} · {knowledge.guidelines.diagnoses.get(diagnosis.code, '')}\n")
    for row in selection.rows:
        print(f"[{row.id}] {row.window} · {row.scheme} · УУР/УДД {row.evidence}")
        if row.condition:
            print(f"   условие: {row.condition}")
        print(f"   КР: {row.substances_text}")
        print(f"   источник: {row.source}")
        by_substance: dict[str, int] = {}
        for c in selection.candidates:
            if c.row.id == row.id:
                key = c.label.substances[0] if c.label.substances else c.label.trade_name
                by_substance[key] = by_substance.get(key, 0) + 1
        if by_substance:
            print("   препараты в базе: " + ", ".join(f"{k} ({n})" for k, n in sorted(by_substance.items())))
        for advice in selection.advice:
            if advice.row.id == row.id:
                print(f"   совет КР: {advice.text}")
        print()
    for restriction in selection.restrictions:
        print(f"⚠ Ограничение КР: {restriction.rule} ({restriction.source})")
    for restriction in selection.restrictions_to_clarify:
        print(f"? Уточните «{restriction.condition}»: если да — {restriction.rule} ({restriction.source})")
    return 0


# ------------------------------------------------------------------------------------------- basket


def _basket_inputs(args: argparse.Namespace) -> tuple[Knowledge, dict[str, Any], CheckContext, Any, Any]:
    knowledge = _knowledge(args, need_drugs=True)
    data = _read_json(args.file)
    catalog = _drugs(knowledge)
    patient = parse_patient(data.get("patient", {}))
    today = (
        date.fromisoformat(data["today"]) if "today" in data else datetime.now(UTC).astimezone(patient.timezone).date()
    )
    ctx = CheckContext(
        knowledge.version, patient, catalog, knowledge.check_rules, knowledge.interaction_overrides, today
    )
    current = parse_current(data.get("current_therapy", []), catalog)
    draft = parse_draft(data.get("draft", {}), catalog)
    return knowledge, data, ctx, current, draft


def _source_text(source: Source, catalog: DrugCatalog) -> str:
    text = source.document
    if source.ref:
        with contextlib.suppress(KompasError):
            text += f" «{catalog.get(source.ref).trade_name}»"
    return text + (f" {source.section}" if source.section else "")


def _print_report(report: CheckReport, names: dict[str, str], catalog: DrugCatalog) -> None:
    boxes = report.boxes()
    for key, name in names.items():
        item_box = boxes.get(Target(key, TargetPart.ITEM))
        dose_box = boxes.get(Target(key, TargetPart.DOSE))
        print(f"■ {name}")
        print(f"  карточка: {SEVERITY_LABEL[item_box] if item_box else 'без рамки'}", end="")
        print(f" · поле дозы: {SEVERITY_LABEL[dose_box]}" if dose_box else "")
        warnings = report.for_item(key)
        if warnings:
            print("  при наведении:")
        for w in warnings:
            part = " (доза)" if all(t.part is TargetPart.DOSE for t in w.targets) else ""
            src = "; ".join(dict.fromkeys(_source_text(s, catalog) for s in w.sources))
            print(f"    [{w.severity.value}]{part} {_wrap(w.message)}")
            print(f"      источник: {src} · id {w.id}")
        print()


def cmd_basket_check(args: argparse.Namespace) -> int:
    """Load a JSON file with ``patient``, ``current_therapy`` and ``draft``; run the checks; print,
    per card, its box colour and hover text (and dose-field boxes)."""
    knowledge, data, ctx, current, draft = _basket_inputs(args)
    report = check_basket(current, draft, ctx)
    if args.json:
        _print_json(report)
        return 0
    catalog = _drugs(knowledge)
    names = {
        f"current:{c.ref}": f"{(catalog.get(c.label_id).display_name if c.label_id else c.substance)} — уже принимает"
        for c in current
        if c.ref not in draft.stopped
    }
    names |= {i.key: f"{catalog.get(i.label_id).display_name} — добавлен" for i in draft.new_items}
    print(
        f"Проверка корзины · пациент {ctx.patient.age_on(ctx.today)} лет · дата {ctx.today} · "
        f"знания {report.knowledge_version}\n"
    )
    _print_report(report, names, catalog)
    need = report.requiring_justification(frozenset(data.get("previously_justified", [])))
    if need:
        print(f"Для подтверждения нужно обоснование для {len(need)} красных предупреждений (второе подтверждение):")
        for w in need:
            print(f'  --justify {w.id}="..."   ← {w.message[:90]}')
    else:
        print("Красных предупреждений, требующих обоснования, нет.")
    return 0


def cmd_basket_confirm(args: argparse.Namespace) -> int:
    """Try to confirm the same JSON with ``--justify id=text`` pairs; print the problems or the
    confirmed snapshot. Exit code 1 if confirmation is refused."""
    knowledge, data, ctx, current, draft = _basket_inputs(args)
    justifications = dict(pair.split("=", 1) for pair in args.justify or [] if "=" in pair)
    justifications |= data.get("justifications", {})
    catalog = _drugs(knowledge)
    diagnosis = IcdCode.parse(data.get("diagnosis") or str(ctx.patient.diagnoses[0]))
    provenances = {
        item.key: provenance(item.origin, catalog.get(item.label_id), diagnosis, knowledge.guidelines)
        for item in draft.new_items
    }
    previously = frozenset(data.get("previously_justified", []))
    problems = confirmation_problems(current, draft, ctx, justifications, previously, provenances)
    if problems:
        if args.json:
            _print_json({"confirmed": False, "problems": problems})
        else:
            print("Назначение НЕ подтверждено. Что нужно сделать:")
            for p in problems:
                print(
                    f'  - {_wrap(p.message, "    ")}\n    (--justify {p.pointer}="...")'
                    if p.code == "justification_required"
                    else f"  - {p.message}"
                )
        return 1
    snapshot = confirm(
        current, draft, ctx, justifications, previously, provenances, doctor_id=args.doctor, now=datetime.now(UTC)
    )
    if args.json:
        _print_json({"confirmed": True, "prescription": snapshot})
        return 0
    print(f"Назначение подтверждено врачом {snapshot.confirmed_by} · знания {snapshot.knowledge_version}\n")
    for confirmed in snapshot.items:
        item = confirmed.item
        name = catalog.get(item.label_id).display_name if item.label_id else item.substance
        state = "новое" if item.is_new else "продолжается"
        print(f"  {name} — {state}")
        if confirmed.provenance:
            prov = confirmed.provenance
            row = f" ({prov.row.id}: {prov.row.source})" if prov.row else ""
            print(f"    КР: {prov.kr_status.value}{row}; показание в ОХЛП: {prov.label_indicated.value}")
        if confirmed.suggested_dose_text:
            print(f"    рекомендация ОХЛП: {_wrap(confirmed.suggested_dose_text[:200], '      ')}")
    if snapshot.stopped_refs:
        print(f"  отменено: {', '.join(sorted(snapshot.stopped_refs))}")
    saved = len(snapshot.warnings_at_confirmation)
    print(f"\n  сохранено предупреждений: {saved}, обоснований: {len(snapshot.justifications)}")
    return 0


# ------------------------------------------------------------------------------------------- triage


def cmd_triage_text(args: argparse.Namespace) -> int:
    """Triage one free text with the keyword model and print the verdict and final level."""
    knowledge = _knowledge(args)
    model = KeywordModel(knowledge.triage_rules.keywords)
    verdict = model.classify(args.text)
    reason, unsure = model_reason(verdict, knowledge.triage_rules)
    result = combine([reason], [], model_unsure=unsure)
    if args.json:
        _print_json({"verdict": verdict, "result": result})
        return 0
    urgent, attention = model.matches(args.text)
    print(f"Текст: «{args.text}»")
    print(f"Модель: {verdict.model_name} (заглушка до выбора локальной модели)")
    if urgent or attention:
        print(f"Совпадения: {', '.join(urgent + attention)}")
    print(f"Итог: {result.level.label}")
    return 0


def cmd_triage_entry(args: argparse.Namespace) -> int:
    """Triage a diary entry (optionally with history) from JSON and print level, reasons and
    critical hits."""
    knowledge = _knowledge(args)
    data = _read_json(args.file)
    entry = parse_diary_entry(data["entry"])
    history = [parse_diary_entry(h, f"history[{i}]") for i, h in enumerate(data.get("history", []))]
    problems = entry.problems(knowledge.complaints.codes())
    if problems:
        raise SystemExit("Запись дневника некорректна: " + "; ".join(problems))
    rules = knowledge.triage_rules
    reasons, hits = evaluate_entry(
        entry, rules, knowledge.complaints, PatientContext(bool(data.get("on_antidepressant")))
    )
    reasons += evaluate_trend([*history, entry], rules)
    unsure = False
    if entry.text:
        reason, unsure = model_reason(KeywordModel(rules.keywords).classify(entry.text), rules)
        reasons.append(reason)
    result = combine(reasons, hits, model_unsure=unsure)
    if args.json:
        _print_json(result)
        return 0
    print(f"Запись за {entry.day}: {result.level.label.upper()}")
    for hit in result.critical_hits:
        print(f"  ⚠ КРИТИЧНО → алерт врачу сразу: {hit.message}")
    for r in result.reasons:
        print(f"  [{r.level.label}] ({r.source.value}) {r.message}")
    if not result.reasons:
        print("  сигналов нет (показывать рядом с полнотой данных, A12)")
    return 0


# ------------------------------------------------------------------------------------------- parser


def build_parser() -> argparse.ArgumentParser:
    """Define all sub-commands and their options."""
    parser = argparse.ArgumentParser(prog="kompas", description="Клинический компас — проверка логики из терминала")
    parser.add_argument("--knowledge-dir", help="папка knowledge_data (по умолчанию рядом с пакетом)")
    parser.add_argument("--ohlp", help="путь к ohlp_fields_v4.csv (или переменная KOMPAS_OHLP_CSV)")
    parser.add_argument("--json", action="store_true", help="вывод в JSON")
    sub = parser.add_subparsers(dest="command", required=True)

    scales = sub.add_parser("scales", help="шкалы и анкеты").add_subparsers(dest="action", required=True)
    scales.add_parser("list", help="список шкал").set_defaults(func=cmd_scales_list)
    validate = scales.add_parser("validate", help="проверить всю базу знаний")
    validate.add_argument("--limit", type=int, default=15)
    validate.set_defaults(func=cmd_scales_validate)
    score_p = scales.add_parser("score", help="посчитать анкету")
    score_p.add_argument("code", help="код шкалы, например PHQ-9")
    score_p.add_argument("--answers", required=True, help="ответы по порядку пунктов через запятую")
    score_p.set_defaults(func=cmd_scales_score)

    drugs = sub.add_parser("drugs", help="база ОХЛП").add_subparsers(dest="action", required=True)
    search = drugs.add_parser("search", help="поиск препарата")
    search.add_argument("query")
    search.add_argument("--offset", type=int, default=0)
    search.add_argument("--limit", type=int, default=5)
    search.set_defaults(func=cmd_drugs_search)

    select = sub.add_parser("select", help="подбор по КР для диагноза")
    select.add_argument("diagnosis", help="код МКБ-10, например F33.2")
    select.add_argument("--suicide-risk", choices=["low", "high", "unknown"], default="unknown")
    select.add_argument("--pregnant", action="store_true", help="пациентка беременна")
    select.set_defaults(func=cmd_select)

    basket = sub.add_parser("basket", help="корзина врача").add_subparsers(dest="action", required=True)
    check = basket.add_parser("check", help="проверить корзину из JSON")
    check.add_argument("file")
    check.set_defaults(func=cmd_basket_check)
    conf = basket.add_parser("confirm", help="подтвердить назначение")
    conf.add_argument("file")
    conf.add_argument("--justify", action="append", metavar="ID=ТЕКСТ", help="обоснование красного предупреждения")
    conf.add_argument("--doctor", default="doctor-demo")
    conf.set_defaults(func=cmd_basket_confirm)

    triage = sub.add_parser("triage", help="дневник: оценка срочности").add_subparsers(dest="action", required=True)
    text = triage.add_parser("text", help="оценить свободный текст")
    text.add_argument("text")
    text.set_defaults(func=cmd_triage_text)
    entry = triage.add_parser("entry", help="оценить запись дневника из JSON")
    entry.add_argument("file")
    entry.set_defaults(func=cmd_triage_entry)

    serve = sub.add_parser("serve", help="запустить API (нужны переменные KOMPAS_*)")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.set_defaults(func=cmd_serve)
    worker = sub.add_parser("worker", help="фоновый обработчик: алерты, модель дневника, уведомления")
    worker.add_argument("--topics", nargs="*", help="только эти темы (например alert.created)")
    worker.add_argument("--once", action="store_true", help="одна итерация")
    worker.set_defaults(func=cmd_worker)
    doctor = sub.add_parser("create-doctor", help="создать учётную запись врача")
    doctor.add_argument("email")
    doctor.add_argument("full_name")
    doctor.add_argument("--specialty", default="психиатр")
    doctor.add_argument("--password", help="по умолчанию спросит в терминале")
    doctor.set_defaults(func=cmd_create_doctor)
    admin = sub.add_parser("create-admin", help="создать администратора (раздаёт роль врача)")
    admin.add_argument("email")
    admin.add_argument("full_name")
    admin.add_argument("--password", help="по умолчанию спросит в терминале")
    admin.set_defaults(func=cmd_create_admin)
    grant = sub.add_parser("grant-doctor", help="дать зарегистрированному аккаунту роль врача")
    grant.add_argument("email")
    grant.add_argument("--specialty", default=None)
    grant.set_defaults(func=cmd_grant_doctor)
    revoke = sub.add_parser("revoke-doctor", help="снять роль врача (если у врача нет пациентов)")
    revoke.add_argument("email")
    revoke.set_defaults(func=cmd_revoke_doctor)
    users = sub.add_parser("users", help="список аккаунтов (без демо)")
    users.add_argument("--role", choices=[r.value for r in Role], default=None)
    users.set_defaults(func=cmd_users)
    return parser


# ------------------------------------------------------------------------------------------- server


def cmd_serve(args: argparse.Namespace) -> int:
    """Run the API with uvicorn (settings from ``KOMPAS_*`` environment variables)."""
    import uvicorn

    from kompas.api.app import create_app

    uvicorn.run(create_app(), host=args.host, port=args.port)
    return 0


def cmd_worker(args: argparse.Namespace) -> int:
    """Run the background worker until SIGTERM."""
    import asyncio

    from kompas.core.config import load_settings
    from kompas.worker import run

    asyncio.run(run(load_settings(), topics=args.topics or None, once=args.once))
    return 0


def cmd_create_doctor(args: argparse.Namespace) -> int:
    """Create a doctor account (doctors don't self-register)."""
    import asyncio
    import getpass

    from kompas.core.config import load_settings
    from kompas.core.db import create_engine, create_session_factory, transaction
    from kompas.modules.identity.service import create_doctor

    password = args.password or getpass.getpass("Пароль врача: ")
    if len(password) < 10:
        raise SystemExit("Пароль должен быть не короче 10 символов")

    async def go() -> None:
        engine = create_engine(load_settings().database_url)
        try:
            async with transaction(create_session_factory(engine)) as session:
                doctor = await create_doctor(
                    session,
                    email=args.email,
                    password=password,
                    full_name=args.full_name,
                    specialty=args.specialty,
                    now=datetime.now(UTC),
                )
            print(f"Врач создан: {doctor.full_name} ({args.email}), id {doctor.id}")
        finally:
            await engine.dispose()

    asyncio.run(go())
    return 0


def _with_session(work: Callable[[Any], Awaitable[None]]) -> int:
    """Run ``work(session)`` in one transaction against the configured database."""
    import asyncio

    from kompas.core.config import load_settings
    from kompas.core.db import create_engine, create_session_factory, transaction

    async def go() -> None:
        engine = create_engine(load_settings().database_url)
        try:
            async with transaction(create_session_factory(engine)) as session:
                await work(session)
        finally:
            await engine.dispose()

    asyncio.run(go())
    return 0


def _password(args: argparse.Namespace, prompt: str) -> str:
    import getpass

    password: str = args.password or getpass.getpass(prompt)
    if len(password) < 10:
        raise SystemExit("Пароль должен быть не короче 10 символов")
    return password


def cmd_create_admin(args: argparse.Namespace) -> int:
    """Create an admin account (admins are never created from the website)."""
    from kompas.modules.identity import accounts

    password = _password(args, "Пароль администратора: ")

    async def work(session: Any) -> None:
        user = await accounts.create_admin(
            session, email=args.email, password=password, full_name=args.full_name, now=datetime.now(UTC)
        )
        print(f"Администратор создан: {args.full_name} ({user.email})")

    return _with_session(work)


def cmd_grant_doctor(args: argparse.Namespace) -> int:
    """Give a registered account (role pending) the doctor role."""
    from kompas.modules.identity import accounts

    async def work(session: Any) -> None:
        user = await accounts.user_by_email(session, args.email)
        doctor = await accounts.grant_doctor(session, None, user.id, specialty=args.specialty, now=datetime.now(UTC))
        print(f"{user.email}: теперь врач ({doctor.full_name}, {doctor.specialty})")

    return _with_session(work)


def cmd_revoke_doctor(args: argparse.Namespace) -> int:
    """Take the doctor role away (the doctor must have no patients)."""
    from kompas.modules.identity import accounts

    async def work(session: Any) -> None:
        user = await accounts.user_by_email(session, args.email)
        await accounts.revoke_doctor(session, None, user.id, now=datetime.now(UTC))
        print(f"{user.email}: роль врача снята (аккаунт без роли)")

    return _with_session(work)


def cmd_users(args: argparse.Namespace) -> int:
    """Print accounts (newest first), as an admin would see them."""
    import uuid

    from kompas.core.actor import Actor
    from kompas.modules.identity import accounts

    cli_admin = Actor(user_id=uuid.UUID(int=0), role=Role.ADMIN, doctor_id=None, patient_id=None)

    async def work(session: Any) -> None:
        role = Role(args.role) if args.role else None
        rows, total = await accounts.list_users(session, cli_admin, query=None, role=role, offset=0, limit=500)
        for r in rows:
            extra = f" · пациентов: {r.patients}" if r.patients is not None else ""
            extra += f" · врач: {r.doctor_name}" if r.doctor_name else ""
            print(f"{r.user.role.value:8} {r.user.email:40} {r.name}{extra}")
        print(f"всего: {total}")

    return _with_session(work)


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point for the ``kompas`` console script. Returns the process exit code; domain errors
    are printed as messages with their details, not tracebacks."""
    args = build_parser().parse_args(argv)
    func: Callable[[argparse.Namespace], int] = args.func
    try:
        return func(args)
    except KompasError as exc:
        print(f"Ошибка: {exc.message}", file=sys.stderr)
        for problem in exc.details:
            where = f" [{problem.pointer}]" if problem.pointer else ""
            print(f"  - {problem.message}{where}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
