/** Running state of a clinician scale form (pure, tested). The final score comes from the server. */
import type { ScaleForm } from "../../../../api/types";

export function answeredCount(form: ScaleForm, answers: Record<string, number>): number {
  return form.items.filter((i) => answers[String(i.id)] !== undefined).length;
}

/** Plain sum of the answered items, shown while filling in. */
export function runningSum(form: ScaleForm, answers: Record<string, number>): number {
  return form.items.reduce((sum, i) => sum + (answers[String(i.id)] ?? 0), 0);
}
