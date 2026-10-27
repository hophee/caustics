/**
 * Questionnaire flow (boards P-Question → P-Result): one item per step, then `POST /me/responses`.
 * The patient never sees a score, band or result (D-109); the response body has none to show anyway.
 */
import type { Assignment, ScaleForm, ScaleListItem } from "../../api/types";
import { dayMonthLong, plural } from "../../lib/format";
import { validScaleAnswer, visibleScaleItems } from "../../lib/scaleForm";

export type Answers = Record<string, number>;

export interface ResponsePayload {
  submission_id: string;
  scale_code: string;
  answers: Record<string, number>;
  assignment_id: string | null;
}

export function answeredCount(scale: ScaleForm, answers: Answers): number {
  return visibleScaleItems(scale, answers).filter((item) => validScaleAnswer(item, answers[item.id])).length;
}

export function isComplete(scale: ScaleForm, answers: Answers): boolean {
  return answeredCount(scale, answers) === visibleScaleItems(scale, answers).length;
}

/** Body of `POST /me/responses`: answers keyed by item id; only items of this scale. */
export function buildResponsePayload(
  scale: ScaleForm,
  answers: Answers,
  submissionId: string,
  assignmentId: string | null,
): ResponsePayload {
  const out: Record<string, number> = {};
  for (const item of visibleScaleItems(scale, answers)) {
    const value = answers[item.id];
    if (value !== undefined) out[String(item.id)] = value;
  }
  return { submission_id: submissionId, scale_code: scale.code, answers: out, assignment_id: assignmentId };
}

/** Rough time to fill: about 20 seconds per item, at least one minute. */
export function minutes(items: number): number {
  return Math.max(1, Math.round(items / 3));
}

function addDays(isoDate: string, days: number): Date {
  const d = new Date(`${isoDate}T00:00:00`);
  d.setDate(d.getDate() + days);
  return d;
}

/** «9 вопросов · около 3 минут · до 27 октября» (the deadline only for a one-off assignment). */
export function assignmentLine(assignment: Assignment, scale: Pick<ScaleListItem, "items"> | undefined): string {
  const parts: string[] = [];
  if (scale) {
    const n = scale.items;
    const m = minutes(n);
    parts.push(`${n} ${plural(n, "вопрос", "вопроса", "вопросов")}`);
    parts.push(`около ${m} ${plural(m, "минуты", "минут", "минут")}`);
  }
  if (assignment.mode === "once") parts.push(`до ${dayMonthLong(addDays(assignment.starts_on, assignment.due_days))}`);
  return parts.join(" · ");
}
