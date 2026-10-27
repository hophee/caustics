/**
 * Regimens as the backend returns them, in two shapes:
 * - prescription items keep the doctor's input: `{intakes: [{at: "21:00", amount: "25"}], unit, start, duration_days}`;
 * - current therapy uses the domain shape: `{schedule: {kind: "daily", intakes: [{at: "21:00:00", amount_mg}]}, start, duration_days}`
 *   or `{schedule: {kind: "prn", dose_mg, max_doses_per_day}, ...}`.
 * Both are normalised here for display.
 */
import { dayMonth, dayMonthLong, plural } from "../../../../lib/format";

export interface ShownRegimen {
  intakes: { at: string; amount: string; unit: string }[];
  prn: { dose: string; unit: string; maxPerDay: number } | null;
  start: string | null;
  durationDays: number | null;
}

type Raw = Record<string, unknown>;

const isObj = (v: unknown): v is Raw => typeof v === "object" && v !== null && !Array.isArray(v);

function num(v: unknown): string {
  const n = Number(v);
  return Number.isFinite(n) ? String(n) : String(v ?? "");
}

const hhmm = (v: unknown) => String(v ?? "").slice(0, 5);

export function normalizeRegimen(raw: unknown): ShownRegimen | null {
  if (!isObj(raw)) return null;
  const start = typeof raw.start === "string" ? raw.start : null;
  const durationDays = typeof raw.duration_days === "number" ? raw.duration_days : null;
  const unit = typeof raw.unit === "string" ? raw.unit : "мг";
  const schedule = isObj(raw.schedule) ? raw.schedule : null;
  if (schedule?.kind === "prn") {
    return {
      intakes: [],
      prn: { dose: num(schedule.dose_mg), unit: "мг", maxPerDay: Number(schedule.max_doses_per_day) || 0 },
      start,
      durationDays,
    };
  }
  if (isObj(raw.prn)) {
    const prn = raw.prn;
    return {
      intakes: [],
      prn: { dose: num(prn.dose), unit: typeof prn.unit === "string" ? prn.unit : unit, maxPerDay: Number(prn.max_per_day) || 0 },
      start,
      durationDays,
    };
  }
  const list = Array.isArray(schedule?.intakes) ? schedule.intakes : Array.isArray(raw.intakes) ? raw.intakes : [];
  const intakes = list.filter(isObj).map((i) =>
    "amount_mg" in i
      ? { at: hhmm(i.at), amount: num(i.amount_mg), unit: "мг" }
      : { at: hhmm(i.at), amount: num(i.amount), unit: typeof i.unit === "string" ? i.unit : unit },
  );
  return { intakes, prn: null, start, durationDays };
}

/** «25 мг в 21:00», «25 мг в 08:00, 50 мг в 21:00», «0.5 мг при необходимости, до 2 раз в сутки» */
export function doseText(r: ShownRegimen | null): string {
  if (!r) return "Схема неизвестна";
  if (r.prn) return `${r.prn.dose} ${r.prn.unit} при необходимости, до ${r.prn.maxPerDay} ${plural(r.prn.maxPerDay, "раза", "раз", "раз")} в сутки`;
  if (r.intakes.length === 0) return "Схема неизвестна";
  return r.intakes.map((i) => `${i.amount} ${i.unit} в ${i.at}`).join(", ");
}

export function endDate(start: string, durationDays: number): Date {
  const d = new Date(`${start}T00:00:00`);
  d.setDate(d.getDate() + durationDays - 1);
  return d;
}

/** «25.10 — 21.11 · 28 дней» */
export function courseText(r: ShownRegimen | null): string {
  if (!r?.start || !r.durationDays) return "";
  const days = r.durationDays;
  return `${dayMonth(r.start)} — ${dayMonth(endDate(r.start, days))} · ${days} ${plural(days, "день", "дня", "дней")}`;
}

/** «с 25 октября по 21 ноября» (patient wording) */
export function periodText(r: ShownRegimen | null): string {
  if (!r?.start || !r.durationDays) return "";
  return `с ${dayMonthLong(r.start)} по ${dayMonthLong(endDate(r.start, r.durationDays))}`;
}
