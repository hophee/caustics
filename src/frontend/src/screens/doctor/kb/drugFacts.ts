/**
 * ОХЛП record (`GET /drugs/{label_id}`) → rows «section · name · value · quote» (A04: every value
 * with its source; «нет данных» when the label has none — nothing is filled in).
 */
import type { Fact } from "../../../api/types";
import { labelText } from "../../../lib/labelText";

export const NO_DATA = "нет данных";

const STATUS: Record<string, string> = {
  contraindicated: "противопоказано",
  caution: "с осторожностью",
  dose_adjustment: "коррекция дозы",
  no_adjustment: "без коррекции дозы",
  not_affected: "не влияет",
  no_data: NO_DATA,
};

export interface FactRow {
  key: string;
  name: string;
  section: string | null;
  value: string;
  missing: boolean;
  /** Level of a normalised status value, for colouring. */
  level: "red" | "amb" | null;
  quote: string | null;
}

interface Spec {
  key: string;
  name: string;
  /** Path inside the label; defaults to `key`. */
  path?: string[];
  format?: (value: unknown) => string | null;
}

const years = (value: unknown) => (typeof value === "number" ? `с ${value} лет` : null);

/** Facts in label order (sections 4.1 → 4.7, then 2). */
export const FACTS: Spec[] = [
  { key: "indications", name: "Показания" },
  { key: "min_age", name: "Возраст", format: years },
  { key: "dose.start_text", name: "Начальная доза", path: ["dose", "start_text"] },
  { key: "dose.frequency_text", name: "Кратность и схема", path: ["dose", "frequency_text"] },
  { key: "dose.max_text", name: "Максимальная доза", path: ["dose", "max_text"] },
  { key: "elderly", name: "Пожилой возраст" },
  { key: "renal", name: "Нарушение функции почек" },
  { key: "renal_gfr_threshold", name: "Пороги клиренса креатинина, мл/мин" },
  { key: "hepatic", name: "Нарушение функции печени" },
  { key: "hepatic_child_pugh", name: "Классы по Чайлд-Пью" },
  { key: "contraindicated_icd", name: "Противопоказания, МКБ-10" },
  { key: "caution_conditions", name: "С осторожностью" },
  { key: "contraindicated_with", name: "Нельзя сочетать с группами" },
  { key: "caution_with", name: "С осторожностью с группами" },
  { key: "patient_level_contraindicated", name: "Противопоказано при приёме" },
  { key: "patient_level_caution", name: "С осторожностью при приёме" },
  { key: "named_interactions", name: "Взаимодействия, МНН" },
  { key: "pregnancy", name: "Беременность" },
  { key: "lactation", name: "Грудное вскармливание" },
  { key: "driving", name: "Управление транспортом" },
  { key: "excipients", name: "Вспомогательные вещества" },
];

/** The short card on the search page (board K-Drugs). */
export const PREVIEW_KEYS = ["indications", "dose.start_text", "contraindicated_icd", "named_interactions", "excipients"];

function isFact(value: unknown): value is Fact<unknown> {
  return typeof value === "object" && value !== null && "value" in value && "quote" in value && "section" in value;
}

function at(label: Record<string, unknown>, path: string[]): unknown {
  let current: unknown = label;
  for (const part of path) {
    if (typeof current !== "object" || current === null) return undefined;
    current = (current as Record<string, unknown>)[part];
  }
  return current;
}

export function formatValue(value: unknown): string | null {
  if (value === null || value === undefined) return null;
  if (Array.isArray(value)) {
    const parts = value.map((v) => labelText(String(v))).filter(Boolean);
    return parts.length > 0 ? parts.join(", ") : null;
  }
  if (typeof value === "string") {
    const text = value.trim();
    if (text in STATUS) return text === "no_data" ? null : (STATUS[text] ?? text);
    return labelText(text) || null;
  }
  return String(value);
}

function rowOf(key: string, name: string, fact: Fact<unknown>, format?: (value: unknown) => string | null): FactRow {
  const value = (format ?? formatValue)(fact.value);
  const level = fact.value === "contraindicated" ? "red" : fact.value === "caution" || fact.value === "dose_adjustment" ? "amb" : null;
  return {
    key,
    name,
    section: fact.section ?? null,
    value: value ?? NO_DATA,
    missing: value === null,
    level,
    quote: (fact.quote && labelText(fact.quote)) || null,
  };
}

/** All facts of a label: the known ones in order, then any other fact-shaped field the API adds. */
export function factRows(label: Record<string, unknown>, keys?: string[]): FactRow[] {
  const specs = keys ? FACTS.filter((s) => keys.includes(s.key)) : FACTS;
  const rows: FactRow[] = [];
  for (const spec of specs) {
    const fact = at(label, spec.path ?? [spec.key]);
    if (isFact(fact)) rows.push(rowOf(spec.key, spec.name, fact, spec.format));
    else rows.push({ key: spec.key, name: spec.name, section: null, value: NO_DATA, missing: true, level: null, quote: null });
  }
  if (!keys) {
    const known = new Set(FACTS.map((s) => (s.path ?? [s.key])[0]));
    for (const [key, value] of Object.entries(label)) {
      if (!known.has(key) && isFact(value)) rows.push(rowOf(key, key, value));
    }
  }
  return rows;
}

/** «200 мг/сут» from the parsed maximum; null when the label's maximum didn't parse. */
export function maxDaily(label: { dose?: { max_daily_mg?: unknown } }): string | null {
  const mg = Number(label.dose?.max_daily_mg);
  if (!label.dose?.max_daily_mg || Number.isNaN(mg)) return null;
  return `${mg.toLocaleString("ru-RU")} мг/сут`;
}

/** Search results grouped by the first active substance, in the API's order. */
export function groupBySubstance<T extends { substances: string[]; atc: string | null }>(items: T[]): { name: string; atc: string | null; items: T[] }[] {
  const groups = new Map<string, { name: string; atc: string | null; items: T[] }>();
  for (const item of items) {
    const name = item.substances.length > 0 ? item.substances.join(" + ") : "—";
    const group = groups.get(name) ?? { name, atc: item.atc, items: [] };
    if (group.atc !== item.atc) group.atc = null;
    group.items.push(item);
    groups.set(name, group);
  }
  return [...groups.values()];
}

export function capitalize(text: string): string {
  return text.charAt(0).toLocaleUpperCase("ru") + text.slice(1);
}
