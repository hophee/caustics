/** Russian formatting of dates, ages and plurals used across the screens. */

const pad = (n: number) => String(n).padStart(2, "0");

function toDate(value: string | Date): Date {
  if (value instanceof Date) return value;
  // A plain date ("1992-03-01") is a calendar date, not a UTC instant.
  return /^\d{4}-\d{2}-\d{2}$/.test(value) ? new Date(`${value}T00:00:00`) : new Date(value);
}

/** 01.03.1992 */
export function date(value: string | Date): string {
  const d = toDate(value);
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}.${d.getFullYear()}`;
}

/** 24.10 */
export function dayMonth(value: string | Date): string {
  const d = toDate(value);
  return `${pad(d.getDate())}.${pad(d.getMonth() + 1)}`;
}

/** 15:48 */
export function time(value: string | Date): string {
  const d = toDate(value);
  return `${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

function sameDay(a: Date, b: Date): boolean {
  return a.getFullYear() === b.getFullYear() && a.getMonth() === b.getMonth() && a.getDate() === b.getDate();
}

/** «сегодня, 08:14» / «вчера, 22:10» / «24.10, 15:47» */
export function when(value: string | Date, now: Date = new Date()): string {
  const d = toDate(value);
  const yesterday = new Date(now);
  yesterday.setDate(now.getDate() - 1);
  if (sameDay(d, now)) return `сегодня, ${time(d)}`;
  if (sameDay(d, yesterday)) return `вчера, ${time(d)}`;
  return `${dayMonth(d)}, ${time(d)}`;
}

const WEEKDAYS = ["воскресенье", "понедельник", "вторник", "среда", "четверг", "пятница", "суббота"];
const MONTHS_GEN = ["января", "февраля", "марта", "апреля", "мая", "июня", "июля", "августа", "сентября", "октября", "ноября", "декабря"];
const WEEKDAYS_SHORT = ["вс", "пн", "вт", "ср", "чт", "пт", "сб"];

/** «Суббота, 25 октября» */
export function longDay(value: string | Date): string {
  const d = toDate(value);
  const day = WEEKDAYS[d.getDay()] ?? "";
  return `${day.charAt(0).toUpperCase()}${day.slice(1)}, ${d.getDate()} ${MONTHS_GEN[d.getMonth()] ?? ""}`;
}

/** «25 октября» */
export function dayMonthLong(value: string | Date): string {
  const d = toDate(value);
  return `${d.getDate()} ${MONTHS_GEN[d.getMonth()] ?? ""}`;
}

export function weekdayShort(value: string | Date): string {
  return WEEKDAYS_SHORT[toDate(value).getDay()] ?? "";
}

/** Russian plural: plural(5, "год", "года", "лет") → "лет". */
export function plural(n: number, one: string, few: string, many: string): string {
  const mod10 = n % 10;
  const mod100 = n % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
}

export function ageYears(birthDate: string, now: Date = new Date()): number {
  const b = toDate(birthDate);
  let age = now.getFullYear() - b.getFullYear();
  if (now.getMonth() < b.getMonth() || (now.getMonth() === b.getMonth() && now.getDate() < b.getDate())) age -= 1;
  return age;
}

/** «34 года» */
export function age(birthDate: string, now?: Date): string {
  const n = ageYears(birthDate, now);
  return `${n} ${plural(n, "год", "года", "лет")}`;
}

/** «34 г.» */
export function ageShort(birthDate: string, now?: Date): string {
  return `${ageYears(birthDate, now)} г.`;
}

/** «Анна Смирнова» → «АС» */
export function initials(fullName: string): string {
  return fullName
    .split(/\s+/)
    .filter(Boolean)
    .slice(0, 2)
    .map((part) => part.charAt(0).toUpperCase())
    .join("");
}

export function sexLabel(sex: string | null | undefined): string {
  if (sex === "female") return "женский";
  if (sex === "male") return "мужской";
  return "Неизвестно";
}
