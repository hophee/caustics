/**
 * The doctor's draft (basket) as pure functions: regimen forms → backend regimens, draft payloads for
 * check and confirm, check report → per-card boxes and warnings, 422 justification handling.
 *
 * Safety notes:
 * - A dose is never invented. The only place a non-doctor amount appears is the *check probe*: an
 *   item whose intakes are not filled yet is checked with a placeholder intake so its item-level
 *   warnings (lactation, interactions, ...) show at once (A05). The probe never reaches confirm, is
 *   never displayed, and the item's dose box is marked as unchecked instead of using its result.
 * - Warnings are never filtered by severity or kind; every warning that targets a card is listed on it.
 */
import type { CheckReport, Severity, Warning } from "../../../../api/types";
import { labelText } from "../../../../lib/labelText";

export type Origin = { kind: "search" } | { kind: "kr_suggestion"; row_id: string };

export interface IntakeForm {
  at: string;
  /** Free text as typed: «25 мг», «0,5 г», «25» (= мг). */
  dose: string;
}

export interface RegimenForm {
  intakes: IntakeForm[];
  start: string; // YYYY-MM-DD (date input)
  duration: string; // days, as typed
}

export interface DraftEntry {
  replaces?: string;
  key: string;
  labelId: string;
  origin: Origin;
  /** «Амитриптилин (амитриптилин), таблетки» */
  title: string;
  /** Guideline row behind a suggestion: source, window. */
  context: string;
  regimen: RegimenForm;
}

export interface DraftState {
  items: DraftEntry[];
  stopped: string[];
}

export const EMPTY_DRAFT: DraftState = { items: [], stopped: [] };

/** Backend regimen (input shape of `parse_regimen`). */
export interface RegimenOut {
  intakes: { at: string; amount: string; unit: string }[];
  start: string;
  duration_days: number;
}

export interface DraftOut {
  new_items: { key: string; label_id: string; origin: Origin; regimen: RegimenOut }[];
  stopped: string[];
}

export interface RegimenErrors {
  intakes: { at?: string; dose?: string }[];
  start?: string;
  duration?: string;
}

export const CURRENT_PREFIX = "current:";

const UNITS: Record<string, string> = { мг: "мг", mg: "мг", г: "г", g: "г", мкг: "мкг", mcg: "мкг" };

/** «25 мг» → {amount: "25", unit: "мг"}; «0,5 г» → {"0.5", "г"}; «25» → мг. Null if not a positive mass. */
export function parseDose(text: string): { amount: string; unit: string } | null {
  const match = /^\s*(\d+(?:[.,]\d+)?)\s*([a-zа-яё]+)?\.?\s*$/i.exec(text);
  if (!match?.[1]) return null;
  const amount = match[1].replace(",", ".");
  const unit = match[2] ? UNITS[match[2].toLowerCase()] : "мг";
  if (!unit || !(Number(amount) > 0)) return null;
  return { amount: String(Number(amount)), unit };
}

/** «9:00» / «21.00» / «21:00» → «09:00» / «21:00»; null if not a time of day. */
export function parseTime(text: string): string | null {
  const match = /^\s*([01]?\d|2[0-3])[:.]([0-5]\d)\s*$/.exec(text);
  if (!match?.[1] || !match[2]) return null;
  return `${match[1].padStart(2, "0")}:${match[2]}`;
}

/** «28», «28 дней» → 28; null unless a positive whole number of days. */
export function parseDuration(text: string): number | null {
  const match = /^\s*(\d+)\s*(д[а-яё]*\.?)?\s*$/i.exec(text);
  if (!match?.[1]) return null;
  const days = Number(match[1]);
  return days > 0 ? days : null;
}

function validDate(text: string): boolean {
  if (!/^\d{4}-\d{2}-\d{2}$/.test(text)) return false;
  const d = new Date(`${text}T00:00:00`);
  return !Number.isNaN(d.getTime());
}

/** The intakes part of a form, or null with per-intake errors. */
function buildIntakes(form: RegimenForm): { intakes: RegimenOut["intakes"] | null; errors: RegimenErrors["intakes"] } {
  const errors: RegimenErrors["intakes"] = [];
  const intakes: RegimenOut["intakes"] = [];
  const seen = new Set<string>();
  form.intakes.forEach((raw, i) => {
    const error: { at?: string; dose?: string } = {};
    const at = parseTime(raw.at);
    const dose = parseDose(raw.dose);
    if (!at) error.at = raw.at.trim() ? "Время ЧЧ:ММ" : "Укажите время";
    else if (seen.has(at)) error.at = "Время повторяется";
    if (!dose) error.dose = raw.dose.trim() ? "Доза в мг, г или мкг" : "Укажите дозу";
    if (at) seen.add(at);
    errors[i] = error;
    if (at && dose && !error.at) intakes.push({ at, amount: dose.amount, unit: dose.unit });
  });
  const ok = form.intakes.length > 0 && errors.every((e) => !e.at && !e.dose);
  return { intakes: ok ? intakes : null, errors };
}

/** Form → backend regimen; `regimen` is null when anything is missing or invalid (see `errors`). */
export function buildRegimen(form: RegimenForm): { regimen: RegimenOut | null; errors: RegimenErrors } {
  const { intakes, errors: intakeErrors } = buildIntakes(form);
  const errors: RegimenErrors = { intakes: intakeErrors };
  if (!validDate(form.start)) errors.start = "Укажите дату начала";
  const duration = parseDuration(form.duration);
  if (duration === null) errors.duration = form.duration.trim() ? "Целое число дней" : "Укажите длительность";
  if (!intakes || errors.start || errors.duration || duration === null) return { regimen: null, errors };
  return { regimen: { intakes, start: form.start, duration_days: duration }, errors };
}

export function hasErrors(errors: RegimenErrors): boolean {
  return Boolean(errors.start || errors.duration || errors.intakes.some((e) => e.at || e.dose));
}

/** Placeholder intake for checking an item whose dose is not filled in yet. Never confirmed. */
const PROBE_INTAKE = { at: "00:00", amount: "0.001", unit: "мг" };

/**
 * Payload for `POST /basket/check`. Items with incomplete intakes are checked with the probe intake
 * (their keys are returned in `unchecked`: the dose part of those cards is "not checked yet").
 */
export function draftForCheck(state: DraftState, today: string): { draft: DraftOut; unchecked: Set<string> } {
  const unchecked = new Set<string>();
  const new_items = state.items.map((item) => {
    const { intakes } = buildIntakes(item.regimen);
    if (!intakes) unchecked.add(item.key);
    const duration = parseDuration(item.regimen.duration);
    return {
      key: item.key,
      label_id: item.labelId,
      origin: item.origin,
      regimen: {
        intakes: intakes ?? [PROBE_INTAKE],
        start: validDate(item.regimen.start) ? item.regimen.start : today,
        duration_days: duration ?? 1,
      },
    };
  });
  return { draft: { new_items, stopped: [...state.stopped] }, unchecked };
}

/** Payload for `POST /basket/confirm`, or the per-item errors that block it. */
export function draftForConfirm(state: DraftState): { draft: DraftOut | null; errors: Record<string, RegimenErrors> } {
  const errors: Record<string, RegimenErrors> = {};
  const new_items: DraftOut["new_items"] = [];
  for (const item of state.items) {
    const built = buildRegimen(item.regimen);
    if (built.regimen) new_items.push({ key: item.key, label_id: item.labelId, origin: item.origin, regimen: built.regimen });
    else errors[item.key] = built.errors;
  }
  if (Object.keys(errors).length > 0) return { draft: null, errors };
  return { draft: { new_items, stopped: [...state.stopped] }, errors };
}

export function isEmptyDraft(state: DraftState): boolean {
  return state.items.length === 0 && state.stopped.length === 0;
}

/** A fresh card key; never collides with `current:` keys or existing cards. */
export function newKey(state: DraftState): string {
  let n = state.items.length + 1;
  const keys = new Set(state.items.map((i) => i.key));
  while (keys.has(`n${n}`)) n += 1;
  return `n${n}`;
}

// --- Severity and check report -------------------------------------------------------------------

const RANK: Record<Severity, number> = { contraindicated: 3, caution: 2, no_data: 1 };

export function worst(severities: Iterable<Severity | null | undefined>): Severity | null {
  let result: Severity | null = null;
  for (const s of severities) {
    if (s && (!result || RANK[s] > RANK[result])) result = s;
  }
  return result;
}

export function bySeverity(a: Warning, b: Warning): number {
  return RANK[b.severity] - RANK[a.severity];
}

export interface CardCheck {
  /** Box colour of the whole card (`part: item`). */
  item: Severity | null;
  /** Box colour of the dose field (`part: dose`); `unchecked` when the dose is not filled in. */
  dose: Severity | "unchecked" | null;
  /** Every warning that targets this card (item or dose part), worst first. */
  warnings: Warning[];
  /** Warnings that target the dose field of this card. */
  doseWarnings: Warning[];
}

/** Boxes and warnings of one card. `unchecked` = keys checked with the probe intake. */
export function cardCheck(report: CheckReport | undefined, key: string, unchecked: Set<string> = new Set()): CardCheck {
  if (!report) return { item: null, dose: unchecked.has(key) ? "unchecked" : null, warnings: [], doseWarnings: [] };
  const warnings = report.warnings.filter((w) => w.targets.some((t) => t.item_key === key)).sort(bySeverity);
  const doseWarnings = warnings.filter((w) => w.targets.some((t) => t.item_key === key && t.part === "dose"));
  const box = (part: "item" | "dose") => worst(report.boxes.filter((b) => b.key === key && b.part === part).map((b) => b.severity));
  return {
    item: box("item"),
    dose: unchecked.has(key) ? "unchecked" : worst([box("dose"), ...doseWarnings.map((w) => w.severity)]),
    warnings,
    doseWarnings,
  };
}

/** Warnings that target no card shown on the page (kept visible in a general block, A05). */
export function unplacedWarnings(report: CheckReport | undefined, keys: Iterable<string>): Warning[] {
  if (!report) return [];
  const known = new Set(keys);
  return report.warnings.filter((w) => !w.targets.some((t) => known.has(t.item_key))).sort(bySeverity);
}

export interface Counts {
  contraindicated: number;
  caution: number;
  no_data: number;
  interactions: number;
}

export function countWarnings(report: CheckReport | undefined): Counts {
  const counts: Counts = { contraindicated: 0, caution: 0, no_data: 0, interactions: 0 };
  for (const w of report?.warnings ?? []) {
    counts[w.severity] += 1;
    if (w.kind === "interaction") counts.interactions += 1;
  }
  return counts;
}

/** Short cause for a pill: «Грудное вскармливание» from «Грудное вскармливание: противопоказано (…)». */
export function shortCause(w: Warning): string {
  if (w.kind === "interaction") return "Взаимодействие";
  if (w.kind === "duplicate") return "Дублирование";
  if (w.kind === "group_load") return "Нагрузка группы";
  if (w.kind === "dose") return "Доза";
  const head = w.message.split(":")[0]?.trim() ?? "";
  return head && head.length <= 32 ? head : w.message.slice(0, 32).trim();
}

/** «ОХЛП 4.3», «КР 301_3, п. 3.1.3» */
export function sourceLabel(source: { document: string; section: string | null }): string {
  return source.section ? `${source.document} ${source.section}` : source.document;
}

/** Label quotes keep list bullets, private-use glyphs and table marks from the PDF (see `labelText`). */
export function cleanQuote(quote: string): string {
  return labelText(quote);
}

/** Wraps in «» unless the text already starts with a quote mark (КР quotes do). */
export function quoted(text: string): string {
  return /^[«"„]/.test(text) ? text : `«${text}»`;
}

// --- Picker probes --------------------------------------------------------------------------------

/**
 * Picker pills from one combined check of the current draft plus every listed label (`probe` keys).
 * A warning counts for a probe when it targets that probe and no other probe (warnings between two
 * listed labels say nothing about adding either one). The selected label is re-checked alone.
 */
export function probeWarnings(report: CheckReport | undefined, probeKeys: string[]): Map<string, Warning[]> {
  const result = new Map<string, Warning[]>(probeKeys.map((k) => [k, []]));
  const probes = new Set(probeKeys);
  for (const w of report?.warnings ?? []) {
    const hit = [...new Set(w.targets.map((t) => t.item_key).filter((k) => probes.has(k)))];
    if (hit.length === 1 && hit[0]) result.get(hit[0])?.push(w);
  }
  for (const list of result.values()) list.sort(bySeverity);
  return result;
}

// --- Confirm errors -------------------------------------------------------------------------------

export interface ConfirmProblems {
  /** warning id → server message, for each `justification_required` problem. */
  justifications: Map<string, string>;
  /** Other problems (invalid regimen, nothing to confirm, ...), as server messages. */
  other: { code: string; message: string; pointer: string | null }[];
}

export function confirmProblems(details: { code: string; message: string; pointer?: string | null }[]): ConfirmProblems {
  const justifications = new Map<string, string>();
  const other: ConfirmProblems["other"] = [];
  for (const d of details) {
    if (d.code === "justification_required" && d.pointer) justifications.set(d.pointer, d.message);
    else other.push({ code: d.code, message: d.message, pointer: d.pointer ?? null });
  }
  return { justifications, other };
}

/**
 * Where each requested justification is written: on the first new card its warning targets, else on
 * the first targeted card shown, else in the general block (`unplaced`).
 */
export function placeJustifications(
  required: Iterable<string>,
  report: CheckReport | undefined,
  newKeys: string[],
  shownKeys: string[],
): { byKey: Map<string, string[]>; unplaced: string[] } {
  const byKey = new Map<string, string[]>();
  const unplaced: string[] = [];
  const fresh = new Set(newKeys);
  const shown = new Set(shownKeys);
  for (const id of required) {
    const warning = report?.warnings.find((w) => w.id === id);
    const keys = warning?.targets.map((t) => t.item_key) ?? [];
    const key = keys.find((k) => fresh.has(k)) ?? keys.find((k) => shown.has(k));
    if (!key) {
      unplaced.push(id);
      continue;
    }
    byKey.set(key, [...(byKey.get(key) ?? []), id]);
  }
  return { byKey, unplaced };
}

/** Justifications to send: only for the warnings the server asked about, trimmed. */
export function justificationsPayload(required: Iterable<string>, texts: Record<string, string>): Record<string, string> {
  const out: Record<string, string> = {};
  for (const id of required) {
    const text = texts[id]?.trim();
    if (text) out[id] = text;
  }
  return out;
}

/** Keep only stops that still refer to current items (after a reload of the base). */
export function rebase(state: DraftState, currentRefs: string[]): DraftState {
  const refs = new Set(currentRefs);
  const stopped = state.stopped.filter((r) => refs.has(r));
  return stopped.length === state.stopped.length ? state : { ...state, stopped };
}

/**
 * The draft plus one probe item per listed label (keys `probe:<i>`), for picker pills. Probes use the
 * placeholder intake: only item-level warnings are read from them (see `probeWarnings`).
 */
export function withProbes(base: DraftOut, labelIds: string[], today: string): { draft: DraftOut; keyOf: Map<string, string> } {
  const keyOf = new Map<string, string>();
  const unique = [...new Set(labelIds)];
  const probes = unique.map((labelId, i) => {
    const key = `probe:${i}`;
    keyOf.set(labelId, key);
    const origin: Origin = { kind: "search" };
    return { key, label_id: labelId, origin, regimen: { intakes: [PROBE_INTAKE], start: today, duration_days: 1 } };
  });
  return { draft: { new_items: [...base.new_items, ...probes], stopped: base.stopped }, keyOf };
}

/** Today in local time as YYYY-MM-DD. */
export function isoToday(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

/** A new card: no dose, no time, no duration (A04: the doctor fills them in); start today. */
export function newEntry(
  state: DraftState,
  label: { labelId: string; title: string; origin: Origin; context: string },
  today: string,
): DraftEntry {
  return { key: newKey(state), ...label, regimen: { intakes: [{ at: "", dose: "" }], start: today, duration: "" } };
}
