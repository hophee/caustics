/** Geometry of the small per-metric / per-scale charts (pure, tested). */
import { type Period, column } from "./period";

export interface ChartPoint {
  day: string;
  value: number;
  critical?: boolean;
}

export interface Box {
  width: number;
  height: number;
  left: number;
  right: number;
  top: number;
  bottom: number;
}

export interface Placed extends ChartPoint {
  x: number;
  y: number;
}

export function xOf(period: Period, day: string, box: Box): number {
  const n = period.days.length;
  const col = column(period, day) ?? 0;
  const span = box.width - box.left - box.right;
  return box.left + (n <= 1 ? span : (col / (n - 1)) * span);
}

export function yOf(value: number, min: number, max: number, box: Box): number {
  const span = box.height - box.top - box.bottom;
  const t = max === min ? 0 : (Math.min(Math.max(value, min), max) - min) / (max - min);
  return box.height - box.bottom - t * span;
}

/**
 * Points inside the period, placed in the box. With `breakOnGap`, a missing day splits the line
 * (a diary day without an entry is not interpolated); otherwise all points form one line.
 */
export function lines(period: Period, points: ChartPoint[], min: number, max: number, box: Box, breakOnGap: boolean): Placed[][] {
  const inside = points
    .filter((p) => column(period, p.day) !== null)
    .sort((a, b) => a.day.localeCompare(b.day))
    .map((p) => ({ ...p, x: xOf(period, p.day, box), y: yOf(p.value, min, max, box) }));
  if (!breakOnGap) return inside.length ? [inside] : [];
  const out: Placed[][] = [];
  let current: Placed[] = [];
  let prevCol: number | null = null;
  for (const p of inside) {
    const col = column(period, p.day) ?? 0;
    if (prevCol !== null && col - prevCol > 1) {
      out.push(current);
      current = [];
    }
    current.push(p);
    prevCol = col;
  }
  if (current.length) out.push(current);
  return out;
}

export function mean(values: number[]): number | null {
  return values.length ? values.reduce((a, b) => a + b, 0) / values.length : null;
}

export function polyline(points: { x: number; y: number }[]): string {
  return points.map((p) => `${p.x.toFixed(1)},${p.y.toFixed(1)}`).join(" ");
}
