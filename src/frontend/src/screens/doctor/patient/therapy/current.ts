/** Current therapy refs joined with the prescription versions that hold their names and provenance. */
import type { CurrentRef, PrescribedItem, PrescriptionVersion } from "./api";

export interface CurrentView {
  ref: string;
  key: string;
  labelId: string | null;
  /** Trade name and form from the prescription; null for drugs known only from the patient. */
  item: PrescribedItem | null;
  /** The version that last prescribed this course. */
  version: PrescriptionVersion | null;
  substance: string | null;
  regimen: unknown;
  patientReported: boolean;
}

/** Newest version first (as the API returns them); the first version listing a course wins. */
export function joinCurrent(current: CurrentRef[], versions: PrescriptionVersion[]): CurrentView[] {
  return current.map((c) => {
    let item: PrescribedItem | null = null;
    let version: PrescriptionVersion | null = null;
    for (const v of versions) {
      const hit = v.items.find((i) => i.course_id === c.ref);
      if (hit) {
        item = hit;
        version = v;
        break;
      }
    }
    return {
      ref: c.ref,
      key: `current:${c.ref}`,
      labelId: c.label_id,
      item,
      version,
      substance: c.substance,
      regimen: c.regimen,
      patientReported: c.patient_reported,
    };
  });
}

/** label id → trade name, from every prescribed item (for source captions). */
export function labelNames(versions: PrescriptionVersion[]): Map<string, string> {
  const names = new Map<string, string>();
  for (const v of versions) for (const i of v.items) if (i.label_id && !names.has(i.label_id)) names.set(i.label_id, i.trade_name);
  return names;
}

/** warning id → the version (and text) where it was justified, newest first. */
export function justifiedIn(versions: PrescriptionVersion[]): Map<string, { version: PrescriptionVersion; text: string }> {
  const map = new Map<string, { version: PrescriptionVersion; text: string }>();
  for (const v of versions) for (const j of v.justifications) if (!map.has(j.warning_id)) map.set(j.warning_id, { version: v, text: j.text });
  return map;
}

/** «Амитриптилин · таблетки» */
export function itemTitle(view: CurrentView, fallback?: string): string {
  if (view.item) return `${view.item.trade_name} · ${view.item.form}`;
  return fallback ?? view.substance ?? "Препарат";
}
