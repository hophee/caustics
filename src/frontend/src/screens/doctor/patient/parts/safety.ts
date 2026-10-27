/**
 * The safety-profile form: explicit answers only. A list fact is «Нет» (`[]`), «Есть» (items) or
 * «Неизвестно» (`null`); the PUT body always carries the full profile (pure, tested).
 */
import type { Profile, Tristate } from "../../../../api/types";

export type ListState = "none" | "some" | "unknown";

export interface ListField {
  state: ListState;
  items: string[];
}

export const LIST_KEYS = ["allergies", "past_intolerance", "intolerant_excipients", "past_inefficacy"] as const;
export type ListKey = (typeof LIST_KEYS)[number];

export interface SafetyForm {
  pregnant: Tristate;
  lactating: Tristate;
  substance_use: Tristate;
  suicide_risk: Profile["suicide_risk"];
  lists: Record<ListKey, ListField>;
  comorbid: string[];
  weight: string;
}

export function listField(list: string[] | null): ListField {
  if (list === null) return { state: "unknown", items: [] };
  return { state: list.length ? "some" : "none", items: [...list] };
}

export function listValue(field: ListField): string[] | null {
  if (field.state === "unknown") return null;
  if (field.state === "none") return [];
  return field.items;
}

export function formOf(profile: Profile, comorbid: string[]): SafetyForm {
  return {
    pregnant: profile.pregnant,
    lactating: profile.lactating,
    substance_use: profile.substance_use,
    suicide_risk: profile.suicide_risk,
    lists: {
      allergies: listField(profile.allergies),
      past_intolerance: listField(profile.past_intolerance),
      intolerant_excipients: listField(profile.intolerant_excipients),
      past_inefficacy: listField(profile.past_inefficacy),
    },
    comorbid: [...comorbid],
    weight: profile.weight_kg == null ? "" : String(profile.weight_kg).replace(".", ","),
  };
}

/** Adds an item once (case-insensitive), trimmed. */
export function addItem(items: string[], value: string): string[] {
  const v = value.trim();
  if (!v || items.some((i) => i.toLowerCase() === v.toLowerCase())) return items;
  return [...items, v];
}

/** `GET /drugs/vocabulary`: names from the ОХЛП base (МНН and excipients normalised, as the checks compare). */
export interface Vocabulary {
  substances: string[];
  trade_names: { name: string; substances: string[] }[];
  excipients: string[];
}

/** Lists picked from the base instead of typed: the checks only match МНН and flagged excipients. */
export const PICKED_FROM: Partial<Record<ListKey, "drugs" | "excipients">> = {
  past_intolerance: "drugs",
  intolerant_excipients: "excipients",
  past_inefficacy: "drugs",
};

/** One dropdown row: `values` are what gets stored (a trade name stores its МНН). */
export interface Suggestion {
  key: string;
  label: string;
  mnn: string | null;
  values: string[];
}

const norm = (v: string) => v.toLowerCase().replace(/ё/g, "е").replace(/[®™]/g, "").replace(/\s+/g, " ").trim();

/** МНН and trade names (or excipients) containing `query`; prefix matches first, then alphabetical. */
export function suggest(vocabulary: Vocabulary, kind: "drugs" | "excipients", query: string, limit = 8): Suggestion[] {
  const q = norm(query);
  const all: Suggestion[] =
    kind === "excipients"
      ? vocabulary.excipients.map((e) => ({ key: e, label: e, mnn: null, values: [e] }))
      : [
          ...vocabulary.substances.map((s) => ({ key: s, label: s, mnn: null, values: [s] })),
          ...vocabulary.trade_names
            .filter((t) => !(t.substances.length === 1 && norm(t.name) === t.substances[0]))
            .map((t) => ({ key: `t:${t.name}`, label: t.name, mnn: t.substances.join(" + "), values: t.substances })),
        ];
  if (!q) return kind === "excipients" ? all.slice(0, limit) : [];
  return all
    .map((s) => ({ s, at: norm(s.label).indexOf(q) }))
    .filter((r) => r.at >= 0)
    .sort((a, b) => Number(a.at > 0) - Number(b.at > 0) || a.s.label.localeCompare(b.s.label, "ru"))
    .slice(0, limit)
    .map((r) => r.s);
}

/** True when a stored item is a name from the base (old free-text items are not matched by the checks). */
export function inVocabulary(vocabulary: Vocabulary, kind: "drugs" | "excipients", item: string): boolean {
  const names = kind === "excipients" ? vocabulary.excipients : vocabulary.substances;
  return names.includes(norm(item));
}

const ICD = /^[A-Z]\d{2}(\.\d{1,2})?$/;

/** «f41.1 » → «F41.1»; null when it is not an ICD-10 code. */
export function normalizeIcd(value: string): string | null {
  const v = value.trim().toUpperCase().replace(",", ".");
  return ICD.test(v) ? v : null;
}

export function parseWeight(value: string): number | null | "invalid" {
  const v = value.trim().replace(",", ".");
  if (!v) return null;
  const n = Number(v);
  return Number.isFinite(n) && n > 0 && n < 400 ? n : "invalid";
}

export interface FormErrors {
  lists: Partial<Record<ListKey, string>>;
  weight?: string;
}

export function validate(form: SafetyForm): FormErrors | null {
  const errors: FormErrors = { lists: {} };
  for (const key of LIST_KEYS) {
    const field = form.lists[key];
    if (field.state === "some" && field.items.length === 0) errors.lists[key] = "Добавьте хотя бы одно значение";
  }
  if (parseWeight(form.weight) === "invalid") errors.weight = "Вес в килограммах";
  return Object.keys(errors.lists).length || errors.weight ? errors : null;
}

/** Full PUT body: unchanged identity facts from the profile, everything else from the form. */
export function profileBody(profile: Profile, form: SafetyForm, mainCodes: string[]): Record<string, unknown> {
  const weight = parseWeight(form.weight);
  const male = profile.sex === "male";
  return {
    birth_date: profile.birth_date,
    sex: profile.sex,
    timezone: profile.timezone,
    pregnant: male ? profile.pregnant : form.pregnant,
    lactating: male ? profile.lactating : form.lactating,
    substance_use: form.substance_use,
    suicide_risk: form.suicide_risk,
    diagnoses: [...mainCodes, ...form.comorbid.filter((c) => !mainCodes.includes(c))],
    allergies: listValue(form.lists.allergies),
    intolerant_excipients: listValue(form.lists.intolerant_excipients),
    past_intolerance: listValue(form.lists.past_intolerance),
    past_inefficacy: listValue(form.lists.past_inefficacy),
    weight_kg: weight === "invalid" ? profile.weight_kg : weight,
  };
}
