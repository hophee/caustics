import type { CurrentItem } from "../../../../api/types";
/** Mapping API data of one patient to what the card shows. Pure functions (tested in view.test.ts). */
import type { Alert, AlertLevel, Profile, Tristate } from "../../../../api/types";
import { date, dayMonth, time } from "../../../../lib/format";

export const UNKNOWN = "Неизвестно";

/** A list fact: `null` = never asked (unknown), `[]` = explicitly none. */
export function listText(list: string[] | null | undefined): string {
  if (list == null) return UNKNOWN;
  if (list.length === 0) return "нет";
  return list.join(", ");
}

export const TRISTATE: Record<Tristate, string> = { yes: "да", no: "нет", unknown: UNKNOWN };

export const SUICIDE_RISK: Record<Profile["suicide_risk"], string> = { low: "низкий", high: "высокий", unknown: "не оценён" };

/** Pregnancy & lactation are shown for women and for an unknown sex, never for men. */
export function showsPregnancy(sex: string | null | undefined): boolean {
  return sex !== "male";
}

/** The chip next to «Беременность и лактация»: what is positive, else what is unknown. */
export function pregnancyChip(profile: Pick<Profile, "pregnant" | "lactating">): { text: string; cls: string } | null {
  const yes = [profile.pregnant === "yes" && "беременность", profile.lactating === "yes" && "лактация"].filter(Boolean);
  if (yes.length) return { text: yes.join(", "), cls: "chip red" };
  if (profile.pregnant === "unknown" || profile.lactating === "unknown") return { text: "неизвестно", cls: "chip nd" };
  return null;
}

/** Diagnoses in the profile besides the case's own ones (the profile list includes the main one). */
export function comorbid(profileDiagnoses: string[], caseCodes: string[]): string[] {
  const own = new Set(caseCodes);
  return profileDiagnoses.filter((code) => !own.has(code));
}

/** Codes of the case's main diagnosis (the profile keeps the rest as comorbid). */
export function mainCodes(diagnoses: { icd_code: string; is_main: boolean }[] | undefined, fallback: string | null): string[] {
  const main = (diagnoses ?? []).filter((d) => d.is_main).map((d) => d.icd_code);
  return main.length ? main : fallback ? [fallback] : [];
}

function capital(text: string): string {
  return text ? text.charAt(0).toUpperCase() + text.slice(1) : text;
}

function num(value: unknown): number | null {
  if (typeof value === "number") return value;
  if (typeof value === "string" && value.trim() !== "" && !Number.isNaN(Number(value))) return Number(value);
  return null;
}

function mg(value: number): string {
  return `${String(Number(value.toFixed(3))).replace(".", ",")} мг`;
}

export interface TherapyView {
  key: string;
  name: string;
  dose: string | null;
  times: string[];
  start: string | null;
  days: number | null;
  reported: boolean;
}

/**
 * One item of GET /patients/{id}/therapy/current. Real shape: `{ref, label_id, substance, regimen:
 * {schedule: {kind: "daily", intakes: [{at, amount_mg}]} | {kind: "prn", dose_mg, …}, start,
 * duration_days} | null, patient_reported}`; `trade_name` is used when present.
 */
export function therapyItem(item: CurrentItem | object): TherapyView {
  const raw = item as Record<string, unknown>;
  const regimen = (raw.regimen ?? null) as Record<string, unknown> | null;
  const schedule = (regimen?.schedule ?? null) as Record<string, unknown> | null;
  const intakes = ((schedule?.intakes ?? regimen?.intakes ?? []) as Record<string, unknown>[]).filter(Boolean);
  const amounts = intakes.map((i) => num(i.amount_mg ?? i.amount)).filter((v): v is number => v !== null);
  const prn = num(schedule?.dose_mg);
  let dose: string | null = null;
  if (amounts.length && amounts.every((a) => a === amounts[0])) dose = mg(amounts[0] ?? 0);
  else if (amounts.length) dose = amounts.map(mg).join(" + ");
  else if (prn !== null) dose = `${mg(prn)} по потребности`;
  const name = String(raw.trade_name ?? raw.substance ?? raw.label_id ?? "Препарат");
  return {
    key: String(raw.ref ?? raw.course_id ?? name),
    name: capital(name),
    dose,
    times: intakes.map((i) => String(i.at ?? "").slice(0, 5)).filter(Boolean),
    start: typeof regimen?.start === "string" ? regimen.start : null,
    days: num(regimen?.duration_days),
    reported: raw.patient_reported === true,
  };
}

/** «Амитриптилин 25 мг» */
export function therapyTitle(item: TherapyView): string {
  return item.dose ? `${item.name} ${item.dose}` : item.name;
}

/** «в 21:00 · с 25.10, 28 дней» */
export function therapyLine(item: TherapyView): string {
  const parts = [];
  if (item.times.length) parts.push(`в ${item.times.join(", ")}`);
  const since = [item.start && `с ${dayMonth(item.start)}`, item.days && `${item.days} дн.`].filter(Boolean).join(", ");
  if (since) parts.push(since);
  if (item.reported) parts.push("со слов пациента");
  return parts.join(" · ");
}

const LEVEL_ORDER: Record<AlertLevel, number> = { urgent: 0, high: 1, planned: 2 };

/** Open alerts of one patient, most severe first, then newest. */
export function patientAlerts(alerts: Alert[], patientId: string): Alert[] {
  return alerts
    .filter((a) => a.patient_id === patientId && a.status !== "handled")
    .sort(
      (a, b) =>
        (LEVEL_ORDER[a.level as AlertLevel] ?? 3) - (LEVEL_ORDER[b.level as AlertLevel] ?? 3) ||
        b.created_at.localeCompare(a.created_at),
    );
}

/** «PHQ-9, пункт 9 = 1» from a scale alert; the reason text otherwise. */
export function alertTitle(alert: Alert): string {
  const details = (alert.details ?? {}) as Record<string, unknown>;
  const source = alert.source.startsWith("scale:") ? alert.source.slice(6) : null;
  if (source && details.item != null && details.value != null) return `${source}, пункт ${String(details.item)} = ${String(details.value)}`;
  const short = alert.reason.split(" — ")[0] ?? alert.reason;
  return short;
}

/** Only the last interaction: «принят 24.10 в 15:48» or «создан …». */
export function alertTrack(alert: Alert): string {
  if (alert.handled_at) return `решение записано ${dayMonth(alert.handled_at)} в ${time(alert.handled_at)}`;
  if (alert.accepted_at) return `принят вами ${dayMonth(alert.accepted_at)} в ${time(alert.accepted_at)} · решение не записано`;
  return `создан ${dayMonth(alert.created_at)} в ${time(alert.created_at)}`;
}

export function caseText(c: { status: string; opened_at: string; closed_at: string | null } | null | undefined): string {
  if (!c) return "нет активного";
  if (c.status === "closed") return `закрыт ${c.closed_at ? date(c.closed_at) : ""}`.trim();
  return `активен с ${dayMonth(c.opened_at)}`;
}

export function weightText(kg: number | null | undefined): string | null {
  return kg == null ? null : `${String(kg).replace(".", ",")} кг`;
}
