/** The course line (board I2-Summary): diary squares, scale results, therapy and signals per day. */
import type { Alert, DoctorResult } from "../../../../api/types";
import { ALERT_LEVEL } from "../../../../ui/levels";
import { dayMonth } from "../../../../lib/format";
import { type Period, addDays, column, localDay } from "./period";
import { bandLabel, criticalText, score } from "./scales";

export type Square = "s" | "a" | "u" | "n" | "x";

const RANK: Record<string, number> = { stable: 1, attention: 2, urgent: 3 };
const SQUARE: Record<number, Square> = { 1: "s", 2: "a", 3: "u" };

/** One square per day: the worst triage of the day; "x" = entry without triage yet, "n" = no entry. */
export function diaryLane(period: Period, entries: { day: string; triage?: { level: string } | null }[]): Square[] {
  const lane: Square[] = period.days.map(() => "n");
  const worst = period.days.map(() => 0);
  for (const entry of entries) {
    const col = column(period, entry.day);
    if (col === null) continue;
    const rank = entry.triage ? (RANK[entry.triage.level] ?? 0) : 0;
    if (rank > (worst[col] ?? 0)) {
      worst[col] = rank;
      lane[col] = SQUARE[rank] ?? "x";
    } else if (lane[col] === "n") lane[col] = "x";
  }
  return lane;
}

export interface ScalePoint {
  col: number;
  day: string;
  label: string;
  critical: boolean;
}

/** Results grouped by day: «PHQ-9 19 · HDRS-17 21», critical when any result has a critical answer. */
export function scaleLane(period: Period, results: DoctorResult[]): ScalePoint[] {
  const byDay = new Map<string, DoctorResult[]>();
  for (const r of results) {
    const day = localDay(r.taken_at);
    if (column(period, day) === null) continue;
    byDay.set(day, [...(byDay.get(day) ?? []), r]);
  }
  return [...byDay.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([day, rs]) => {
      const labels = rs.map((r) => [
        `${r.scale_code} ${r.total == null ? bandLabel(r) ?? "заполнена" : score(r.total)}`,
        ...criticalText(r),
      ].join(" · "));
      return {
        col: column(period, day) ?? 0, day,
        label: [...new Set(labels)].join("; "),
        critical: rs.some((r) => (r.critical_hits ?? []).length > 0),
      };
    });
}

export interface RxCourse {
  course_id?: string;
  trade_name?: string;
  substance?: string | null;
  regimen?: { start?: string; duration_days?: number } | null;
}

export interface RxVersion {
  confirmed_at: string;
  items: unknown[];
  stopped_course_ids: unknown[];
}

export interface Segment {
  kind: "rx" | "obs";
  /** 1-based grid column and span. */
  col: number;
  span: number;
  label: string;
}

function capital(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Courses from the prescription versions: a course runs from its start for `duration_days` or until
 * a later version stops it. Days without any course become «без препаратов» segments.
 */
export function therapyLane(period: Period, versions: RxVersion[]): Segment[] {
  const sorted = [...versions].sort((a, b) => a.confirmed_at.localeCompare(b.confirmed_at));
  const courses = new Map<string, { start: string; end: string; name: string }>();
  for (const version of sorted) {
    const stopDay = localDay(version.confirmed_at);
    for (const id of version.stopped_course_ids) {
      const course = courses.get(String(id));
      if (course && stopDay < course.end) course.end = stopDay;
    }
    for (const raw of version.items as RxCourse[]) {
      const id = String(raw.course_id ?? "");
      const start = raw.regimen?.start;
      if (!id || !start || courses.has(id)) continue;
      const days = raw.regimen?.duration_days ?? 1;
      courses.set(id, { start, end: addDays(start, Math.max(days, 1) - 1), name: capital(String(raw.trade_name ?? raw.substance ?? "")) });
    }
  }
  const covered = period.days.map(() => false);
  const segments: Segment[] = [];
  for (const course of courses.values()) {
    if (course.end < period.start || course.start > period.end) continue;
    const from = column(period, course.start < period.start ? period.start : course.start) ?? 0;
    const to = column(period, course.end > period.end ? period.end : course.end) ?? period.days.length - 1;
    for (let i = from; i <= to; i++) covered[i] = true;
    segments.push({ kind: "rx", col: from + 1, span: to - from + 1, label: course.name });
  }
  let i = 0;
  while (i < covered.length) {
    if (covered[i]) {
      i++;
      continue;
    }
    const from = i;
    while (i < covered.length && !covered[i]) i++;
    segments.push({ kind: "obs", col: from + 1, span: i - from, label: "без препаратов" });
  }
  return segments.sort((a, b) => a.col - b.col);
}

export interface SignalMark {
  col: number;
  cls: string;
  label: string;
  id: string;
}

const ALERT_RANK: Record<string, number> = { urgent: 0, high: 1, planned: 2 };

/** One mark per day: the most severe alert of the day, «×N» when there were several. */
export function signalLane(period: Period, alerts: Alert[]): SignalMark[] {
  const byDay = new Map<number, Alert[]>();
  for (const a of alerts) {
    const col = column(period, localDay(a.created_at));
    if (col !== null) byDay.set(col, [...(byDay.get(col) ?? []), a]);
  }
  return [...byDay.entries()]
    .map(([col, list]) => {
      const top = [...list].sort((a, b) => (ALERT_RANK[a.level] ?? 3) - (ALERT_RANK[b.level] ?? 3))[0] as Alert;
      const meta = ALERT_LEVEL[top.level as keyof typeof ALERT_LEVEL] ?? ALERT_LEVEL.planned;
      const count = list.length > 1 ? ` ×${list.length}` : "";
      return { col: col + 1, cls: meta.cls, label: `${meta.label} ${dayMonth(top.created_at)}${count}`, id: top.alert_id };
    })
    .sort((a, b) => a.col - b.col);
}
