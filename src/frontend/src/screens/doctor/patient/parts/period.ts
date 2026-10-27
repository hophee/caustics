/** Calendar days ("YYYY-MM-DD") and the case period shown on the course line and the charts. */

const DAY_MS = 86_400_000;

function utc(day: string): number {
  const [y, m, d] = day.slice(0, 10).split("-").map(Number);
  return Date.UTC(y ?? 1970, (m ?? 1) - 1, d ?? 1);
}

function fromUtc(ms: number): string {
  return new Date(ms).toISOString().slice(0, 10);
}

/** Local calendar day of an instant (or a Date). */
export function localDay(value: string | Date = new Date()): string {
  const d = value instanceof Date ? value : new Date(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}

export function addDays(day: string, n: number): string {
  return fromUtc(utc(day) + n * DAY_MS);
}

/** Whole days from `a` to `b` (b − a). */
export function daysBetween(a: string, b: string): number {
  return Math.round((utc(b) - utc(a)) / DAY_MS);
}

export interface Period {
  start: string;
  end: string;
  days: string[];
}

/** Keep the newest calendar days that fit without shrinking cells or date labels. */
export function fittingPeriod(period: Period, width: number, reserved = 0): Period {
  const count = Math.max(1, Math.floor((width - reserved) / 28));
  const days = period.days.slice(-count);
  return { start: days[0] ?? period.end, end: period.end, days };
}

export function periodOf(start: string, end: string): Period {
  const n = Math.max(daysBetween(start, end), 0) + 1;
  return { start, end, days: Array.from({ length: n }, (_, i) => addDays(start, i)) };
}

/**
 * The case period up to `today`: from the case opening (or the earliest known event, if earlier),
 * at least `min` days so a fresh case still shows its recent history, at most `max` days.
 */
export function coursePeriod(
  openedOn: string | null | undefined,
  today: string,
  { min = 28, max = 60, earliest }: { min?: number; max?: number; earliest?: (string | null | undefined)[] } = {},
): Period {
  let start = openedOn ? openedOn.slice(0, 10) : today;
  for (const day of earliest ?? []) {
    if (day && day.slice(0, 10) < start) start = day.slice(0, 10);
  }
  const minStart = addDays(today, -(min - 1));
  const maxStart = addDays(today, -(max - 1));
  if (start > minStart) start = minStart;
  if (start < maxStart) start = maxStart;
  return periodOf(start, today);
}

/** 0-based column of `day` in the period, or null outside it. */
export function column(period: Period, day: string): number | null {
  const i = daysBetween(period.start, day.slice(0, 10));
  return i >= 0 && i < period.days.length ? i : null;
}

/** Axis labels: the first day and every 7th day after it (1-based grid columns). */
export function weekTicks(period: Period): { day: string; col: number }[] {
  const ticks = [];
  for (let i = 0; i < period.days.length; i += 7) ticks.push({ day: period.days[i] ?? period.start, col: i + 1 });
  return ticks;
}

/** Two-day labels around events; weekly labels in gaps of at least seven empty days. */
export function eventTicks(period: Period, eventDays: string[]): { day: string; col: number }[] {
  const events = [...new Set(eventDays.map((day) => column(period, day)).filter((i): i is number => i !== null))].sort((a, b) => a - b);
  if (!events.length) return weekTicks(period);
  const runs: { start: number; end: number }[] = [];
  for (const index of events) {
    const run = runs.at(-1);
    if (run && index - run.end <= 7) run.end = index;
    else runs.push({ start: index, end: index });
  }
  const indices = new Set<number>();
  let previousEnd = -1;
  for (const run of runs) {
    for (let i = run.start - 7; i >= 0 && i >= previousEnd + 2; i -= 7) indices.add(i);
    const end = Math.min(period.days.length - 1, run.end + 1);
    for (let i = run.start; i <= end; i += 2) indices.add(i);
    previousEnd = run.start + 2 * Math.floor((end - run.start) / 2);
  }
  for (let i = previousEnd + 7; i < period.days.length; i += 7) indices.add(i);
  return [...indices].sort((a, b) => a - b).map((i) => ({ day: period.days[i]!, col: i + 1 }));
}
