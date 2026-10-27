/** Scale catalogue labels. */

export const FILLED_BY: Record<"patient" | "clinician" | "patient_clinician", { short: string; long: string }> = {
  patient: { short: "пациент", long: "самоопросник" },
  clinician: { short: "врач", long: "заполняет врач" },
  patient_clinician: { short: "пациент / врач", long: "заполняет пациент или врач" },
};

/** «PHQ-9 · опросник здоровья пациента» → «опросник здоровья пациента». */
export function scaleTitle(name: string, code: string): string {
  const prefix = `${code} · `;
  return name.startsWith(prefix) ? name.slice(prefix.length) : name === code ? "" : name;
}
