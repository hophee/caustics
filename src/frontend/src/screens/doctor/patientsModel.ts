/** Mapping of overview summaries (`GET /overview/patients`) to the doctor's list and diary strip. */
import type { AlertLevel, PatientSummary, TriageLevel } from "../../api/types";
import { plural } from "../../lib/format";
import { ALERT_LEVEL, TRIAGE } from "../../ui/levels";

export type Square = "s" | "a" | "u" | "n" | "w";

/** One diary day → a square: triage level, «w» for an entry still being assessed, «n» for no entry. */
export function daySquare(day: { level: string | null; pending: boolean }): Square {
  const meta = day.level ? TRIAGE[day.level as TriageLevel] : undefined;
  if (meta) return meta.sq as Square;
  if (day.pending) return "w";
  return "n";
}

export const SQUARE_TITLE: Record<Square, string> = {
  s: "Стабильно",
  a: "Требует внимания",
  u: "Срочно",
  n: "Нет записи",
  w: "Оценивается",
};

export function squares(days: PatientSummary["days"], last?: number): Square[] {
  const list = last ? days.slice(-last) : days;
  return list.map(daySquare);
}

/** Days without a diary entry after which a patient counts as «Нет записей» (A12 freshness). */
export const STALE_AFTER_DAYS = 3;

export function activityAgo(value: string, now: Date): string {
  const elapsed = Math.max(0, now.getTime() - new Date(value).getTime());
  if (elapsed < 3_600_000) return `${Math.max(1, Math.ceil(elapsed / 60_000))} мин. назад`;
  if (elapsed < 86_400_000) return `${Math.ceil(elapsed / 3_600_000)} ч. назад`;
  const days = Math.ceil(elapsed / 86_400_000);
  return `${days} ${plural(days, "день", "дня", "дней")} назад`;
}

export function daysSince(value: string, now: Date): number {
  const d = new Date(value);
  const a = Date.UTC(d.getFullYear(), d.getMonth(), d.getDate());
  const b = Date.UTC(now.getFullYear(), now.getMonth(), now.getDate());
  return Math.round((b - a) / 86_400_000);
}

export function isStale(p: PatientSummary, now: Date): boolean {
  return !p.last_entry_at || daysSince(p.last_entry_at, now) >= STALE_AFTER_DAYS;
}

export type StatusKey = AlertLevel | "attention" | "stable" | "none";

export const STATUS_ORDER: StatusKey[] = ["urgent", "high", "planned", "attention", "none", "stable"];

export const STATUS_META: Record<StatusKey, { label: string; cls: string }> = {
  urgent: ALERT_LEVEL.urgent,
  high: ALERT_LEVEL.high,
  planned: ALERT_LEVEL.planned,
  attention: { label: TRIAGE.attention.label, cls: TRIAGE.attention.cls },
  none: { label: "Нет записей", cls: "lv-gry" },
  stable: { label: TRIAGE.stable.label, cls: TRIAGE.stable.cls },
};

/**
 * The patient's filter status: the most urgent open alert; otherwise «Нет записей» when the diary is
 * stale; otherwise the level of the latest assessed diary day.
 */
export function patientStatus(p: PatientSummary, now: Date = new Date()): StatusKey {
  if (p.open_alerts > 0 && p.top_alert_level && p.top_alert_level in ALERT_LEVEL) return p.top_alert_level as AlertLevel;
  if (isStale(p, now)) return "none";
  const latest = [...p.days].reverse().find((d) => d.level);
  if (latest?.level === "urgent") return "urgent";
  if (latest?.level === "attention") return "attention";
  if (latest?.level === "stable") return "stable";
  return "none";
}

export function statusCounts(list: PatientSummary[], now: Date = new Date()): { key: StatusKey; count: number }[] {
  const counts = new Map<StatusKey, number>();
  for (const p of list) {
    const key = patientStatus(p, now);
    counts.set(key, (counts.get(key) ?? 0) + 1);
  }
  return STATUS_ORDER.filter((key) => counts.has(key)).map((key) => ({ key, count: counts.get(key) ?? 0 }));
}

function norm(text: string): string {
  return text.toLocaleLowerCase("ru").replace(/ё/g, "е").replace(/\s+/g, " ").trim();
}

/** Search by name (any word order, «ё» = «е») or ICD code prefix («F33», «f33.2»). */
export function matchesQuery(p: PatientSummary, q: string): boolean {
  const query = norm(q);
  if (!query) return true;
  const name = norm(p.full_name);
  const icd = norm(p.main_icd ?? "");
  if (icd && icd.startsWith(query)) return true;
  return query.split(" ").every((word) => name.includes(word) || (icd !== "" && icd.startsWith(word)));
}

export function filterPatients(list: PatientSummary[], q: string, status: StatusKey | null, now: Date = new Date()): PatientSummary[] {
  return list.filter((p) => matchesQuery(p, q) && (!status || patientStatus(p, now) === status));
}
