"""Convert supplied OHLP tables without modifying them; join quotations strictly by source file.

Usage: python tools/import_ohlp_workbook.py SOURCE.xlsx OUTPUT.csv
The 'Все препараты' sheet is an index, not a safety dataset, and is not silently promoted to labels.
Requires openpyxl. Unknown fields stay blank; interaction membership is resolved by the existing core.
"""
import csv
import json
import sys
from pathlib import Path
import openpyxl

FIELDS = {
    'Торговое название': 'trade', 'МНН': 'mnn', 'АТХ': 'atx', 'Показания': 'indications',
    'Мин. возраст': 'min_age', 'Беременность': 'preg', 'Лактация': 'lact', 'Пожилые': 'elderly',
    'Почки': 'renal', 'СКФ, мл/мин': 'renal_gfr', 'Печень': 'hepatic', 'Чайлд-Пью': 'hepatic_childpugh',
    'Противопоказания': 'contra_conditions', 'Противопоказания: коды МКБ': 'contra_icd',
    'С осторожностью': 'caution_conditions', 'Вспомогательные вещества': 'excip_flags',
    'Нельзя с': 'int_contra', 'Осторожно с': 'int_caution', 'Вещества, упомянутые во взаимодействиях (4.5)': 'int_named',
    'Доза начальная': 'dose_start', 'Доза макс.': 'dose_max', 'Ед.': 'dose_unit', 'Кратность': 'dose_freq',
    'Вождение': 'driving', 'Профиль АД (КР)': 'ad_profile', 'Седация (КР)': 'ad_sed',
    'Инсомния / ажитация (КР)': 'ad_ins', 'Профиль АД: источник': 'ad_profile_src',
    'Сверить с ОХЛП': 'needs_check', 'Почему сверить': 'needs_check_reason', 'Файл': 'file',
}

def records(sheet):
    rows = iter(sheet.values)
    headers = next(rows)
    return [dict(zip(headers, row)) for row in rows if any(v is not None for v in row)]

def convert(source: Path, output: Path):
    book = openpyxl.load_workbook(source, data_only=True, read_only=True)
    rows = records(book['Психиатрия'])
    quotes = records(book['Цитаты (психиатрия)'])
    keyed = {r['Файл']: r for r in quotes}
    assert len(keyed) == len(quotes), 'Duplicate quote source file'
    assert len({r['Файл'] for r in rows}) == len(rows), 'Duplicate drug source file'
    assert {r['Файл'] for r in rows} == set(keyed), 'Drug and quote tables do not match'
    result = []
    for row in rows:
        item = {key: '' if row.get(header) is None else str(row[header]) for header, key in FIELDS.items()}
        for header, value in keyed[row['Файл']].items():
            for suffix, code_suffix in [(': цитата', '_quote'), (': раздел', '_sec')]:
                if header and header.endswith(suffix):
                    stem = header[:-len(suffix)]
                    assert stem in FIELDS, stem
                    item[FIELDS[stem] + code_suffix] = '' if value is None else str(value)
        item.update(in_scope='да', int_groups='', mnn_source='предоставленная таблица ОХЛП')
        result.append(item)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open('w', encoding='utf-8-sig', newline='') as fh:
        writer = csv.DictWriter(fh, fieldnames=list(result[0]))
        writer.writeheader()
        writer.writerows(result)
    manifest = {'source': source.name, 'labels': len(result), 'joined_by': 'Файл',
        'index_only_records': book['Все препараты'].max_row - 1,
        'note': 'Лист Все препараты содержит только индекс; полного набора ограничений для него нет.'}
    output.with_suffix('.manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(manifest, ensure_ascii=False))

if __name__ == '__main__':
    convert(Path(sys.argv[1]), Path(sys.argv[2]))
