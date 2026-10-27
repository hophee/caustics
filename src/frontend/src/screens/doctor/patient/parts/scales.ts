/** Scale results for the doctor: first/last per scale, the maximum of a scale form. One scale at a time. */
import type { DoctorResult, ScaleForm } from "../../../../api/types";
import { dayMonth, plural } from "../../../../lib/format";
import { addDays, localDay } from "./period";

export interface ScaleRow {
  code: string;
  first: { value: number; day: string };
  last: { value: number; day: string } | null;
  delta: number | null;
  band: string | null;
}

function scored(results: DoctorResult[]): (DoctorResult & { total: number })[] {
  return results.filter((r): r is DoctorResult & { total: number } => typeof r.total === "number");
}

/** One row per scale (never mixing scales): first and last scored result, change, latest band. */
export function scaleRows(results: DoctorResult[]): ScaleRow[] {
  const byScale = new Map<string, (DoctorResult & { total: number })[]>();
  for (const r of scored(results)) byScale.set(r.scale_code, [...(byScale.get(r.scale_code) ?? []), r]);
  return [...byScale.entries()]
    .map(([code, rs]) => {
      const sorted = [...rs].sort((a, b) => a.taken_at.localeCompare(b.taken_at));
      const first = sorted[0];
      const last = sorted.length > 1 ? sorted[sorted.length - 1] : undefined;
      const latest = last ?? first;
      return {
        code,
        first: { value: first?.total ?? 0, day: localDay(first?.taken_at ?? "") },
        last: last ? { value: last.total, day: localDay(last.taken_at) } : null,
        delta: last && first ? last.total - first.total : null,
        band: bandLabel(latest),
      };
    })
    .sort((a, b) => a.code.localeCompare(b.code));
}

export function bandLabel(result: DoctorResult | undefined): string | null {
  const band = result?.band as { label?: string } | null | undefined;
  return band?.label ? band.label.charAt(0).toLowerCase() + band.label.slice(1) : null;
}

/** Highest possible total of a form: sum of each item's largest option. */
export function scaleMax(form: ScaleForm | undefined): number | null {
  if (!form || form.items.length === 0) return null;
  if (form.max_total !== undefined) return form.max_total;
  return form.items.reduce((sum, item) => sum + Math.max(0, ...item.options.map((o) => o.value)), 0);
}

/** Item rules have an item/value; aggregate rules carry a note instead. */
export function criticalText(result: DoctorResult): string[] {
  return (result.critical_hits ?? []).map((hit) => {
    const item = hit.item;
    if (typeof item === "number" || (typeof item === "string" && item.length > 0)) {
      return `пункт ${item}${hit.value == null ? "" : ` = ${String(hit.value)}`}`;
    }
    return typeof hit.note === "string" && hit.note ? hit.note : "Критический критерий";
  });
}

export function score(value: number | null | undefined): string {
  return value == null ? "—" : String(Number(value.toFixed(1))).replace(".", ",");
}

/** Points of one series (one scale or one subscale); critical when that day's result of the scale had one. */
export function seriesPoints(
  series: { scale_code: string; subscale_code: string | null; points: Record<string, unknown>[] },
  results: DoctorResult[],
): { day: string; value: number; critical: boolean }[] {
  const criticalDays = new Set(
    results.filter((r) => r.scale_code === series.scale_code && (r.critical_hits ?? []).length > 0).map((r) => localDay(r.taken_at)),
  );
  return series.points
    .map((p) => ({ day: String(p.taken_on ?? ""), value: Number(p.value) }))
    .filter((p) => p.day && Number.isFinite(p.value))
    .map((p) => ({ ...p, critical: series.subscale_code === null && criticalDays.has(p.day) }));
}

export interface AssignmentLike {
  mode: string;
  every_days: number | null;
  starts_on: string;
  due_days: number;
}

/** «каждые 14 дней с 10.10» / «разово» */
export function assignmentSchedule(a: AssignmentLike): string {
  if (a.mode === "repeat" && a.every_days) {
    return `каждые ${a.every_days} ${plural(a.every_days, "день", "дня", "дней")} с ${dayMonth(a.starts_on)}`;
  }
  return "разово";
}

/** The next time the patient is asked: «следующая 07.11» (repeat) or «до 27.10» (once). */
export function assignmentNext(a: AssignmentLike, today: string): string {
  if (a.mode === "repeat" && a.every_days) {
    let next = a.starts_on;
    while (next < today) next = addDays(next, a.every_days);
    return `следующая ${dayMonth(next)}`;
  }
  const until = addDays(a.starts_on, a.due_days);
  return until < today ? `срок истёк ${dayMonth(until)}` : `до ${dayMonth(until)}`;
}
