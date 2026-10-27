/** Doses, marks, prescription lines and reminders as the patient sees them (boards I2-Today, P-Meds). */
import type { Dose, PrescriptionLine, ReminderRule } from "../../api/types";
import { dayMonthLong, plural } from "../../lib/format";
import { localDay } from "./diaryFlow";

/** The mark already given, in the patient's words; null while the dose can still be marked. */
export function doseStateLabel(state: string): string | null {
  switch (state) {
    case "on_time":
      return "Принял";
    case "late":
      return "Принял с опозданием";
    case "skipped":
      return "Пропустил";
    case "unmarked":
      return "Нет отметки";
    default:
      return null;
  }
}

export function isMarked(dose: Pick<Dose, "mark">): boolean {
  return dose.mark !== null && dose.mark !== undefined;
}

/** «25 мг» / «12.5 мг» */
export function mg(value: number | string | null | undefined): string {
  if (value === null || value === undefined || value === "") return "";
  const n = Number(value);
  return Number.isFinite(n) ? `${String(n).replace(".", ",")} мг` : `${value} мг`;
}

interface IntakeLike {
  at?: unknown;
  amount_mg?: unknown;
  amount?: unknown;
  unit?: unknown;
}

function intakeText(intake: IntakeLike): string {
  let dose = "";
  if (intake.amount_mg !== undefined && intake.amount_mg !== null) dose = mg(intake.amount_mg as number | string);
  else if (intake.amount !== undefined && intake.amount !== null)
    dose = `${String(intake.amount)}${typeof intake.unit === "string" ? ` ${intake.unit}` : ""}`;
  const at = typeof intake.at === "string" ? `в ${intake.at}` : "";
  return [dose, at].filter(Boolean).join(" ");
}

/** «Амитриптилин, таблетки 25 мг» — the strength when every intake has the same amount. */
export function lineTitle(line: PrescriptionLine): string {
  const amounts = new Set(line.intakes.map((i) => String((i as IntakeLike).amount_mg ?? "")));
  const [only] = [...amounts];
  const strength = amounts.size === 1 && only ? ` ${mg(only)}` : "";
  return `${line.trade_name}, ${line.form}${strength}`;
}

/** «25 мг в 21:00 · с 23 октября по 19 ноября» */
export function lineSchedule(line: PrescriptionLine): string {
  const intakes = line.intakes.map((i) => intakeText(i as IntakeLike)).filter(Boolean);
  const parts: string[] = [];
  if (intakes.length) parts.push(intakes.join(", "));
  if (line.as_needed) parts.push(`при необходимости: ${line.as_needed}`);
  parts.push(`с ${dayMonthLong(line.start)} по ${dayMonthLong(line.end)}`);
  return parts.join(" · ");
}

/** Intake times of all lines, sorted and unique («21:00»). */
export function intakeTimes(lines: readonly PrescriptionLine[]): string[] {
  const times = new Set<string>();
  for (const line of lines) for (const i of line.intakes) if (typeof (i as IntakeLike).at === "string") times.add((i as IntakeLike).at as string);
  return [...times].sort();
}

function minutesText(n: number): string {
  return `${n} ${plural(n, "минуту", "минуты", "минут")}`;
}

/** «в 21:00, повтор через 30 минут, не больше 2 раз» */
export function reminderText(rule: ReminderRule, times: readonly string[]): string {
  if (!rule.enabled) return "выключено";
  const offset = rule.first_offset_minutes;
  const at = times.length ? times.join(", ") : "время приёма";
  const first =
    offset === 0
      ? `в ${at}`
      : offset < 0
        ? `за ${minutesText(-offset)} до приёма (${at})`
        : `через ${minutesText(offset)} после приёма (${at})`;
  if (rule.max_repeats === 0) return `${first}, без повторов`;
  return `${first}, повтор через ${minutesText(rule.repeat_every_minutes)}, не больше ${rule.max_repeats} ${plural(rule.max_repeats, "раза", "раз", "раз")}`;
}

export type DayMark = "tk" | "sk" | "nm" | "fu" | "";

/** One calendar day: taken (all doses taken), skipped (any skip), no mark (any unmarked), future. */
export function dayMark(doses: readonly Pick<Dose, "state">[], future: boolean): DayMark {
  if (future) return doses.length ? "fu" : "";
  if (!doses.length) return "";
  const states = doses.map((d) => d.state);
  if (states.includes("skipped")) return "sk";
  if (states.includes("unmarked")) return "nm";
  if (states.every((s) => s === "on_time" || s === "late")) return "tk";
  return "";
}

export interface CalendarDay {
  day: string;
  date: number;
  mark: DayMark;
  today: boolean;
}

function shift(d: Date, days: number): Date {
  const out = new Date(d);
  out.setDate(out.getDate() + days);
  return out;
}

/** Monday of the week `weeks - 1` weeks before today's week, and Sunday of today's week. */
export function calendarRange(today: Date, weeks = 3): { since: string; until: string; start: Date } {
  const mondayOffset = (today.getDay() + 6) % 7;
  const start = shift(today, -mondayOffset - 7 * (weeks - 1));
  return { since: localDay(start), until: localDay(shift(start, weeks * 7 - 1)), start };
}

/** Days of the marks calendar (Mon…Sun rows), each with its mark. */
export function calendarDays(doses: readonly Pick<Dose, "day" | "state">[], today: Date, weeks = 3): CalendarDay[] {
  const { start } = calendarRange(today, weeks);
  const todayKey = localDay(today);
  const out: CalendarDay[] = [];
  for (let i = 0; i < weeks * 7; i++) {
    const d = shift(start, i);
    const key = localDay(d);
    const own = doses.filter((x) => x.day === key);
    const future = key > todayKey || (key === todayKey && own.every((x) => x.state === "upcoming" || x.state === "due"));
    out.push({ day: key, date: d.getDate(), mark: dayMark(own, future), today: key === todayKey });
  }
  return out;
}
