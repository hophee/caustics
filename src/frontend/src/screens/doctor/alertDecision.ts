/** «Что сделано» in the alert dialog → the note sent with `POST /alerts/{id}/handle`. */

export type Sex = "female" | "male" | string | null | undefined;

export function patientWord(sex: Sex, form: "gen" | "ins"): string {
  if (form === "gen") return sex === "female" ? "пациентки" : sex === "male" ? "пациента" : "пациента";
  return sex === "female" ? "пациенткой" : "пациентом";
}

export function actionOptions(sex: Sex): { code: string; label: string }[] {
  return [
    { code: "contacted", label: `Связь с ${patientWord(sex, "ins")} установлена` },
    { code: "visit", label: "Назначен внеочередной приём" },
    { code: "psychiatrist", label: "Направление к психиатру" },
    { code: "emergency", label: "Вызвана экстренная помощь" },
  ];
}

export type Risk = "low" | "high" | "unknown";

export const RISK_LABEL: Record<Risk, string> = { low: "Низкий", high: "Высокий", unknown: "Не оценён" };

export interface Decision {
  actions: string[];
  risk: Risk | null;
  note: string;
}

/** One line per fact, in the order shown; empty when nothing was recorded. */
export function composeNote(decision: Decision, sex: Sex): string {
  const labels = new Map(actionOptions(sex).map((o) => [o.code, o.label]));
  const lines = actionOptions(sex)
    .map((o) => o.code)
    .filter((code) => decision.actions.includes(code))
    .map((code) => labels.get(code) ?? code);
  if (decision.risk) lines.push(`Суицидальный риск: ${RISK_LABEL[decision.risk].toLocaleLowerCase("ru")}`);
  const note = decision.note.trim();
  if (note) lines.push(note);
  return lines.join("\n");
}
