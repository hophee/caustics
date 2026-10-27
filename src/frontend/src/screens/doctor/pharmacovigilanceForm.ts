/** Fields of the supplied «Извещение о нежелательной реакции…» form, 2026-10-27. */
export type FormField = { key: string; label: string; choices?: string[]; multiline?: boolean; required?: boolean; multiple?: boolean };
export type FormGroup = { title: string; fields: FormField[] };
const drugFields = (prefix: string, required = false): FormField[] => [
  { key: `${prefix}_name`, label: "Торговое название", required },
  { key: `${prefix}_maker`, label: "Производитель" }, { key: `${prefix}_batch`, label: "Номер серии" },
  { key: `${prefix}_dose`, label: "Доза, путь введения" }, { key: `${prefix}_start`, label: "Дата начала терапии" },
  { key: `${prefix}_end`, label: "Дата окончания терапии" }, { key: `${prefix}_indication`, label: "Показание" },
];
export const formGroups: FormGroup[] = [
  { title: "Извещение", fields: [
    { key: "type", label: "Тип сообщения", choices: ["Первичное", "Дополнительная информация"] },
    { key: "previous", label: "Номер и дата предыдущего сообщения (для дополнительного)" },
  ] },
  { title: "Данные пациента", fields: [
    { key: "patient_code", label: "Инициалы или код пациента", required: true },
    { key: "sex", label: "Пол", choices: ["М", "Ж"] }, { key: "weight", label: "Вес, кг" },
    { key: "age", label: "Возраст" }, { key: "pregnant", label: "Беременность", choices: ["Нет", "Есть", "Неизвестно"] },
    { key: "gestation", label: "Срок беременности, недель" },
    { key: "allergy", label: "Аллергия", choices: ["Нет", "Есть", "Неизвестно"] }, { key: "allergy_to", label: "На что аллергия" },
    { key: "care", label: "Лечение", choices: ["Амбулаторное", "Стационарное", "Самолечение"] },
  ] },
  ...Array.from({ length: 3 }, (_, i) => ({ title: `Предполагаемый препарат ${i + 1}`, fields: drugFields(`drug${i}`, i === 0) })),
  { title: "Нежелательная реакция", fields: [
    { key: "onset", label: "Дата начала реакции / сведения о времени начала" },
    { key: "description", label: "Описание реакции (все детали, включая лабораторные исследования)", multiline: true, required: true },
    { key: "resolved", label: "Дата разрешения реакции" },
    { key: "seriousness", label: "Критерии серьёзности", multiple: true, choices: ["Смерть", "Угроза жизни", "Госпитализация или её продление", "Инвалидность", "Врожденные аномалии", "Клинически значимое событие", "Не применимо"] },
    { key: "actions", label: "Предпринятые меры", multiple: true, choices: ["Без лечения", "Отмена подозреваемого препарата", "Снижение дозы", "Немедикаментозная терапия (в т.ч. хирургическое вмешательство)", "Лекарственная терапия"] },
    { key: "treatment", label: "Проведённая терапия", multiline: true },
    { key: "outcome", label: "Исход", choices: ["Выздоровление без последствий", "Улучшение состояния", "Состояние без изменений", "Выздоровление с последствиями", "Смерть", "Неизвестно", "Не применимо"] },
    { key: "sequelae", label: "Последствия (если имеются)" },
    { key: "dechallenge", label: "Сопровождалась ли отмена препарата исчезновением реакции?", choices: ["Нет", "Да", "Препарат не отменялся", "Не применимо"] },
    { key: "rechallenge", label: "Назначалось ли лекарство повторно?", choices: ["Нет", "Да", "Не применимо"] },
    { key: "rechallenge_result", label: "Результат повторного назначения" },
  ] },
  ...Array.from({ length: 5 }, (_, i) => ({ title: `Другой препарат за последние 3 месяца — ${i + 1}`, fields: drugFields(`other${i}`) })),
  { title: "Данные сообщающего лица", fields: [
    { key: "reporter", label: "Кто сообщает", choices: ["Врач", "Другой специалист системы здравоохранения", "Пациент", "Иной"] },
    { key: "region", label: "Субъект РФ или иностранное государство", required: true },
    { key: "contact", label: "Контактный телефон / e-mail", required: true },
    { key: "reporter_name", label: "ФИО" }, { key: "workplace", label: "Должность и место работы" },
    { key: "reported_on", label: "Дата сообщения" },
  ] },
];

export const escapeHtml = (value: string) => value.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" })[c] ?? c);
export function printableForm(fields: Record<string, string>): string {
  const groups = formGroups.map((group) => `<section><h2>${escapeHtml(group.title)}</h2><table>${group.fields.map((f) => `<tr><th>${escapeHtml(f.label)}</th><td>${escapeHtml(fields[f.key] ?? "")}</td></tr>`).join("")}</table></section>`).join("");
  return `<!doctype html><html lang="ru"><meta charset="utf-8"><title>Извещение о нежелательной реакции</title><style>body{font:12px Arial;max-width:900px;margin:24px auto;color:#000}h1{font-size:18px}h2{font-size:14px;margin-top:20px}table{border-collapse:collapse;width:100%}th,td{border:1px solid #888;padding:6px;text-align:left;vertical-align:top;white-space:pre-wrap;overflow-wrap:anywhere}th{width:40%}tr{break-inside:avoid}h2{break-after:avoid}@page{size:A4;margin:15mm}@media print{button{display:none}}</style><body><button onclick="window.print()">Печать / сохранить PDF</button><h1>ИЗВЕЩЕНИЕ О НЕЖЕЛАТЕЛЬНОЙ РЕАКЦИИ ИЛИ ОТСУТСТВИИ ТЕРАПЕВТИЧЕСКОГО ЭФФЕКТА ЛЕКАРСТВЕННОГО ПРЕПАРАТА</h1>${groups}<p>Отправьте заполненное извещение: npr@roszdravnadzor.gov.ru или через external.roszdravnadzor.ru.</p></body></html>`;
}
