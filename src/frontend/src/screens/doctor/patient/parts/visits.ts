/** Labels for visits and referrals (pure, tested). */

export const KIND = {
  consultation: "Консультация",
  examination: "Обследование",
  procedure: "Процедура",
  hospitalization: "Госпитализация",
} as const;

export const URGENCY = { planned: "плановое", priority: "приоритетное", emergency: "экстренное" } as const;

export const CATEGORY = { obligatory: "обязательно", desirable: "желательно" } as const;

const VISIT_LABELS: [string, string][] = [
  ["goal", "Цель"],
  ["complaints", "Жалобы"],
  ["anamnesis", "Анамнез"],
  ["status", "Статус"],
  ["diagnosis", "Диагноз"],
  ["scales", "Шкалы на приёме"],
  ["prescription", "Назначение"],
  ["plan", "План"],
  ["notes", "Заметки"],
];

function text(value: unknown): string {
  if (value == null) return "";
  if (typeof value === "string") return value.trim();
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  if (Array.isArray(value)) return value.map(text).filter(Boolean).join(", ");
  return Object.entries(value as Record<string, unknown>)
    .map(([k, v]) => `${k}: ${text(v)}`)
    .join("; ");
}

/** The visit record as label/value rows: known fields in a fixed order, then the rest; empty ones skipped. */
export function visitFields(content: Record<string, unknown>): { label: string; value: string }[] {
  if (content.schema === "encounter-v1") {
    const labels = [["reason", "Причина обращения"], ["diseaseHistory", "Анамнез болезни / течение с прошлого визита"],
      ["lifeHistory", "Анамнез жизни"], ["diagnosis", "Диагноз"], ["rationale", "Обоснование"], ["uncertainty", "Диагностические сомнения"],
      ["examination", "Осмотр"], ["profile", "Данные пациента"], ["provisional", "Предварительный диагноз"],
      ["therapyConfirmed", "Терапия подтверждена"], ["prescriptionId", "Номер назначения"], ["recommendations", "Рекомендации и консультации"], ["diary", "Дневники"], ["nextVisit", "Следующий приём"], ["worsening", "Действия при ухудшении"]];
    const rows = labels.map(([key, label]) => ({label: label ?? "", value: text(content[key ?? ""])}));
    rows.unshift({label: "Жалобы", value: content.noComplaints ? "Отрицает" : text(content.complaints)});
    rows.unshift({label: "Статус", value: content.status === "completed" ? "Завершён" : "Черновик"});
    rows.push({label: "Суицидальный риск", value: content.risk === "high" ? "Высокий" : content.risk === "low" ? "Низкий" : "Не оценён"});
    const actions: Record<string,string> = {continue:"Продолжить лечение", prescription:"Изменить назначение", dose:"Изменить дозировку", diagnosis:"Изменить диагноз и лечение"};
    rows.push({label:"Решение по терапии",value: actions[String(content.action)] ?? ""});
    if (Array.isArray(content.investigations)) rows.push({label:"Исследования",value: content.investigations.map((i) => typeof i === "object" && i !== null ? [i.name,i.condition,i.due,i.result].filter(Boolean).join(" · ") : "").filter(Boolean).join("\n")});
    return rows.filter((r) => r.value);
  }
  const known = new Set(VISIT_LABELS.map(([k]) => k));
  const rows = VISIT_LABELS.map(([key, label]) => ({ label, value: text(content[key]) }));
  for (const [key, value] of Object.entries(content)) if (!known.has(key)) rows.push({ label: key, value: text(value) });
  return rows.filter((r) => r.value);
}
