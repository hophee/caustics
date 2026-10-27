/**
 * Diary check-in flow (boards P-Diary / P-DiaryText): the step machine and the `POST /me/diary` body.
 * Every answer may be skipped: a skipped score is sent as null, and a yes/no question's null (unusual experiences,
 * the self-harm question) means "not answered" (the backend never reads it as "no").
 */

export type Sleep = "poor" | "normal" | "good";

export type DiaryStep = "mood" | "anxiety" | "irritability" | "sleep" | "unusual" | "self_harm" | "text";

export const STEPS: readonly DiaryStep[] = ["mood", "anxiety", "irritability", "sleep", "unusual", "self_harm", "text"];

/** Three mood categories for new diary entries. */
export const MOODS = [
  { value: 0, label: "Депрессивное", mouth: "M9.5 19.5c2.5-2.5 6.5-2.5 9 0" },
  { value: 1, label: "Нормальное", mouth: "M10 18h8" },
  { value: 2, label: "Повышенное", mouth: "M9.5 16.5c2.5 3 6.5 3 9 0" },
] as const;

export const SLEEP_OPTIONS: readonly { value: Sleep; label: string }[] = [
  { value: "poor", label: "Плохо" },
  { value: "normal", label: "Нормально" },
  { value: "good", label: "Хорошо" },
];

export interface DiaryDraft {
  mood: number | null;
  anxiety: number | null;
  irritability: number | null;
  sleep_hours: number | null;
  unusual_experiences: boolean | null;
  self_harm_thoughts: boolean | null;
  text: string;
}

export const EMPTY_DRAFT: DiaryDraft = {
  mood: null,
  anxiety: null,
  irritability: null,
  sleep_hours: null,
  unusual_experiences: null,
  self_harm_thoughts: null,
  text: "",
};

/** A mood value from the URL (`?mood=1` from «Сегодня»); anything outside 0–2 is ignored. */
export function parseMood(raw: string | null): number | null {
  if (raw === null || !/^\d{1,2}$/.test(raw)) return null;
  const value = Number(raw);
  return value >= 0 && value <= 2 ? value : null;
}

/** Where the flow starts: after the mood step when the mood was already picked on «Сегодня». */
export function firstStep(draft: DiaryDraft): DiaryStep {
  return draft.mood === null ? "mood" : "anxiety";
}

export function stepNumber(step: DiaryStep): number {
  return STEPS.indexOf(step) + 1;
}

export function nextStep(step: DiaryStep): DiaryStep | null {
  return STEPS[STEPS.indexOf(step) + 1] ?? null;
}

export function prevStep(step: DiaryStep): DiaryStep | null {
  const index = STEPS.indexOf(step);
  return index > 0 ? (STEPS[index - 1] ?? null) : null;
}

/** Progress bar width, e.g. step 2 of 7 → "28.6%". */
export function progress(step: DiaryStep): string {
  return `${Math.round((stepNumber(step) / STEPS.length) * 1000) / 10}%`;
}

export function toggle(list: readonly string[], code: string): string[] {
  return list.includes(code) ? list.filter((c) => c !== code) : [...list, code];
}

/** The patient's local calendar day, `YYYY-MM-DD`. */
export function localDay(now: Date = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
}

export interface DiaryPayload {
  submission_id: string;
  day: string;
  mood: number | null;
  anxiety: number | null;
  irritability: number | null;
  sleep_hours: number | null;
  unusual_experiences: boolean | null;
  self_harm_thoughts: boolean | null;
  schema_version: 2;
  text: string | null;
}

/** Body of `POST /me/diary`. The same `submissionId` is reused on a retry (idempotent). */
export function buildDiaryPayload(draft: DiaryDraft, day: string, submissionId: string): DiaryPayload {
  const text = draft.text.trim();
  return {
    submission_id: submissionId,
    day,
    mood: draft.mood,
    anxiety: draft.anxiety,
    irritability: draft.irritability,
    sleep_hours: draft.sleep_hours,
    unusual_experiences: draft.unusual_experiences,
    self_harm_thoughts: draft.self_harm_thoughts,
    schema_version: 2,
    text: text === "" ? null : text,
  };
}

/** The label of a new diary mood category (for the patient's own entry list). */
export function moodLabel(value: number | null): string | null {
  return MOODS.find((mood) => mood.value === value)?.label ?? null;
}

export function sleepLabel(value: string | null): string | null {
  return SLEEP_OPTIONS.find((o) => o.value === value)?.label ?? null;
}

/** Duration, not a time of day: 24:00 is allowed, 24:01 is not. */
export function parseSleepDuration(hours: string, minutes: string): number | null {
  if (hours === "" && minutes === "") return null;
  if (!/^\d{1,2}$/.test(hours) || (minutes !== "" && !/^\d{1,2}$/.test(minutes))) return NaN;
  const h = Number(hours);
  const m = Number(minutes || "0");
  return h <= 24 && m < 60 && (h < 24 || m === 0) ? h + m / 60 : NaN;
}

export function sleepDurationLabel(hours: number): string {
  const minutes = Math.round(hours * 60);
  return `${String(Math.floor(minutes / 60)).padStart(2, "0")}:${String(minutes % 60).padStart(2, "0")}`;
}
