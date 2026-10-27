/** Link texts of the sources behind a diagnosis (board K-Diagnoses). */
import type { CareStandard, GuidelineDocument } from "../../../api/types";
import { date } from "../../../lib/format";

/** «Депрессивный эпизод, Рекуррентное депрессивное расстройство · 2024» */
export function guidelineLabel(doc: GuidelineDocument): string {
  return `${doc.title} · ${doc.year}`;
}

/** «Приказ 745н от 24.08.2026» */
export function standardLabel(standard: CareStandard): string {
  return `Приказ ${standard.number} от ${date(standard.date)}`;
}
