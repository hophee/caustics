export interface Measure { day: string; value: number }
import type { PrescriptionVersion } from "../therapy/api";
import { normalizeRegimen } from "../therapy/regimen";

/** Dose edits create prescription versions, but must not restart efficacy for the same drugs. */
export function therapyStart(versions: PrescriptionVersion[]): string | undefined {
  const ordered = [...versions].sort((a,b) => b.version-a.version);
  const signature = (v: PrescriptionVersion) => v.items.map((i) => i.label_id ?? i.substance ?? i.course_id).sort().join("|");
  let oldest = ordered[0];
  if (!oldest?.items.length) return undefined;
  for (const version of ordered.slice(1)) { if (signature(version) !== signature(oldest)) break; oldest = version; }
  return oldest.items.map((i) => normalizeRegimen(i.regimen)?.start).filter((s): s is string => Boolean(s)).sort().at(-1);
}
export interface Criterion { scale: string; earliestDay: number; checkDays?: number[]; intervalDays?: number; reduction?: number; inclusive?: boolean; text: string; source: string }
// Explicitly transcribed from the supplied project PDF, pp. 13–15. Severity cutoffs are not response thresholds.
export function criterionFor(diagnosis: string): Criterion | undefined {
  const source = "Описание проекта, стр. 13–15";
  if (/^F3[23]/.test(diagnosis)) return {scale: "HDRS-17", earliestDay: 28, reduction: 50, inclusive: true, source,
    text: "КР 301_3: редукция не более 50% после двух последовательных курсов по 3–4 недели входит в определение резистентности. Один курс не устанавливает резистентность."};
  if (/^F41\.[01]/.test(diagnosis)) return {scale: "HARS", earliestDay: 28, checkDays: diagnosis.startsWith("F41.1") ? [7,14,28] : [28], intervalDays: diagnosis.startsWith("F41.1") ? 28 : undefined, reduction: 50, inclusive: true, source,
    text: "КР F41.0 / F41.1: снижение HARS более 50%; оценка для изменения терапии через 4–6 недель. Решение принимает врач."};
  if (diagnosis.startsWith("F43.1")) return {scale: "CAPS", earliestDay: 56, reduction: 70, source,
    text: "КР F43.1: улучшение менее 70% за 8–12 недель на СИОЗС — критерий нонреспонса. Требуется проверить применимость к текущему курсу."};
  if (diagnosis.startsWith("F40")) return {scale: "CGI", earliestDay: 28, source,
    text: "КР F40: оценка через 4–6 недель; числовой порог ответа не установлен."};
}
const days = (a: string, b: string) => Math.floor((Date.parse(b.slice(0, 10)) - Date.parse(a.slice(0, 10))) / 86400000);
export function evaluateCourse(rule: Criterion, start: string | undefined, now: string, measures: Measure[]) {
  if (!start) return {kind: "unknown" as const, text: "Дата начала текущей терапии не указана; срок оценки не рассчитывается."};
  const elapsed = days(start, now);
  if (elapsed < 0) return {kind: "waiting" as const, text: "Дата начала курса ещё не наступила."};
  const points = measures.filter((p) => p.day <= now).sort((a, b) => a.day.localeCompare(b.day));
  const baseline = points.filter((p) => p.day <= start).at(-1);
  const latest = points.filter((p) => p.day > start).at(-1);
  const dueDay = rule.intervalDays && elapsed >= rule.earliestDay ? rule.earliestDay + Math.floor((elapsed - rule.earliestDay) / rule.intervalDays) * rule.intervalDays : (rule.checkDays ?? [rule.earliestDay]).filter((d) => d <= elapsed).at(-1);
  if (dueDay === undefined) return {kind: "waiting" as const, text: `Идёт ${elapsed + 1}-й день терапии. Срок оценки для изменения лечения ещё не наступил.`, baseline, latest};
  if (!latest || days(start, latest.day) < dueDay) return {kind: "missing" as const, text: "Срок наступил, но нового результата нет. Напоминание пройти шкалу; заключение о неэффективности не формируется.", baseline, latest};
  if (elapsed < rule.earliestDay) return {kind: "waiting" as const, text: "Плановое измерение выполнено. Срок оценки для изменения лечения ещё не наступил.", baseline, latest};
  if (!baseline || baseline.value === 0) return {kind: "unknown" as const, text: "Нет пригодного исходного балла для расчёта изменения. Показана фактическая динамика.", baseline, latest};
  const reduction = (baseline.value - latest.value) / baseline.value * 100;
  const text = `${baseline.value} → ${latest.value}; снижение ${reduction.toFixed(1)}% за ${days(start, latest.day)} дней.`;
  if (rule.reduction === undefined) return {kind: "dynamic" as const, text: `${text} Числовой критерий эффективности не задан.`, baseline, latest};
  const below = rule.inclusive ? reduction <= rule.reduction : reduction < rule.reduction;
  return {kind: below ? "review" as const : "dynamic" as const, text, baseline, latest};
}
