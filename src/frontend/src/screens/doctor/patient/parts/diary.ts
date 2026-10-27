/** Diary entries for the doctor: metric lines, chart points, filters, triage source (pure, tested). */
import { sleepDurationLabel } from "../../../patient/diaryFlow";
import type { DoctorDiaryEntry } from "../../../../api/types";
import type { ChartPoint } from "./chart";
import { type Period, column } from "./period";

export const SLEEP: Record<string, string> = { poor: "плохо", normal: "нормально", good: "хорошо" };

type EntryLike = Partial<Pick<DoctorDiaryEntry, "schema_version" | "irritability" | "sleep_hours" | "unusual_experiences">> & Pick<DoctorDiaryEntry, "mood" | "anxiety" | "energy" | "sleep" | "complaints" | "side_effects" | "self_harm_thoughts">;

/** «настроение 4 · тревога 7 · энергия 3 · сон плохо · жалобы: …» — only what was answered. */
export function metricsLine(entry: EntryLike, names: (code: string) => string = (c) => c, full = false): string {
  const parts: string[] = [];
  if (entry.schema_version === 2) {
    if (entry.mood != null) parts.push(`настроение: ${["депрессивное", "нормальное", "повышенное"][entry.mood]}`);
    if (entry.anxiety != null) parts.push(`тревога ${entry.anxiety}/3`);
    if (entry.irritability != null) parts.push(`раздражительность ${entry.irritability}/3`);
    if (entry.sleep_hours != null) parts.push(`сон ${sleepDurationLabel(entry.sleep_hours)}`);
    if (entry.unusual_experiences != null) parts.push(`необычные проявления: ${entry.unusual_experiences ? "да" : "нет"}`);
    if (entry.self_harm_thoughts != null) parts.push(`мысли о самоповреждении: ${entry.self_harm_thoughts ? "да" : "нет"}`);
    return parts.join(" · ");
  }
  if (entry.mood != null) parts.push(`настроение ${entry.mood}`);
  if (entry.anxiety != null) parts.push(`тревога ${entry.anxiety}`);
  if (full && entry.energy != null) parts.push(`энергия ${entry.energy}`);
  if (entry.sleep) parts.push(`сон ${SLEEP[entry.sleep] ?? entry.sleep}`);
  if (full && entry.complaints.length) parts.push(`жалобы: ${entry.complaints.map(names).join(", ").toLowerCase()}`);
  if (full && entry.side_effects.length) parts.push(`побочные эффекты: ${entry.side_effects.map(names).join(", ").toLowerCase()}`);
  if (full && entry.self_harm_thoughts != null) parts.push(`мысли о самоповреждении: ${entry.self_harm_thoughts ? "да" : "нет"}`);
  return parts.join(" · ");
}

export type Metric = "mood" | "anxiety" | "energy";

export const METRICS: { key: Metric; label: string }[] = [
  { key: "mood", label: "Настроение" },
  { key: "anxiety", label: "Тревога" },
  { key: "energy", label: "Энергия" },
];

/** The latest entry per day (a corrected entry has one row per day already). */
function perDay(entries: DoctorDiaryEntry[]): DoctorDiaryEntry[] {
  const byDay = new Map<string, DoctorDiaryEntry>();
  for (const e of entries) {
    const prev = byDay.get(e.day);
    if (!prev || e.saved_at > prev.saved_at) byDay.set(e.day, e);
  }
  return [...byDay.values()].sort((a, b) => a.day.localeCompare(b.day));
}

export function metricPoints(entries: DoctorDiaryEntry[], metric: Metric): ChartPoint[] {
  return perDay(entries)
    .filter((e) => e[metric] != null)
    .map((e) => ({ day: e.day, value: e[metric] as number }));
}

export function latestValue(entries: DoctorDiaryEntry[], metric: Metric): number | null {
  return metricPoints(entries, metric).at(-1)?.value ?? null;
}

const SLEEP_BAR: Record<string, string> = { poor: "sl-p", normal: "sl-m", good: "sl-g" };

/** One bar per day by sleep quality (board i.p / i.m / i.g), x when the day has no answer. */
export function sleepBars(period: Period, entries: DoctorDiaryEntry[]): string[] {
  const bars = period.days.map(() => "x");
  for (const e of perDay(entries)) {
    const col = column(period, e.day);
    if (col !== null && e.sleep) bars[col] = SLEEP_BAR[e.sleep] ?? "x";
  }
  return bars;
}

export type Filter = "all" | "text" | "attention" | "notes" | "side";

export const FILTERS: { key: Filter; label: string }[] = [
  { key: "all", label: "Все" },
  { key: "text", label: "С текстом" },
  { key: "attention", label: "Требуют внимания" },
  { key: "notes", label: "С заметкой" },
  { key: "side", label: "Побочные эффекты" },
];

export function matches(entry: DoctorDiaryEntry, filter: Filter): boolean {
  switch (filter) {
    case "text":
      return Boolean(entry.text?.trim());
    case "attention":
      return entry.triage?.level === "attention" || entry.triage?.level === "urgent";
    case "notes":
      return entry.notes.length > 0;
    case "side":
      return entry.side_effects.length > 0;
    default:
      return true;
  }
}

/** What set the level: the model's name, «правила» or «динамика» (reasons at the entry's level). */
export function triageSource(triage: DoctorDiaryEntry["triage"]): string | null {
  if (!triage) return null;
  const reasons = (triage.reasons ?? []) as { source?: string; level?: string }[];
  const decisive = reasons.filter((r) => r.level === triage.level);
  const sources = new Set(decisive.map((r) => r.source));
  const out: string[] = [];
  if (sources.has("model") && triage.model_name) out.push(triage.model_name);
  if (sources.has("rules")) out.push("правила");
  if (sources.has("trend")) out.push("динамика");
  return out.length ? out.join(" · ") : null;
}

/** Reasons as plain lines for the pill's title. */
export function triageReasons(triage: DoctorDiaryEntry["triage"]): string {
  return ((triage?.reasons ?? []) as { message?: string }[]).map((r) => r.message).filter(Boolean).join("\n");
}
