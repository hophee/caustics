import type { Profile } from "../../../../api/types";

export type VisitStep = "record" | "tests" | "diagnosis" | "labs" | "decision" | "summary";
export type TreatmentAction = "continue" | "prescription" | "dose" | "diagnosis";
export interface Investigation { name: string; condition: string; source: string; due: string; result: string }
export interface Encounter {
  id?: string;
  kind: "first" | "repeat";
  heldAt: string;
  status: "draft" | "completed";
  reason: string;
  complaints: string;
  noComplaints: boolean;
  diseaseHistory: string;
  lifeHistory: string;
  examination: Record<string, string>;
  profile?: Profile;
  diagnosis: string;
  diagnosisSaved: boolean;
  provisional: boolean;
  rationale: string;
  uncertainty: string;
  testFilter?: string;
  investigations: Investigation[];
  investigationDiagnosis?: string;
  action?: TreatmentAction;
  risk?: "low" | "high";
  therapyConfirmed?: boolean;
  prescriptionId?: string;
  recommendations: string;
  nextVisit: string;
  worsening: string;
  diary: string;
}

export function emptyEncounter(): Encounter {
  const d = new Date();
  const local = new Date(d.getTime() - d.getTimezoneOffset() * 60000).toISOString().slice(0, 16);
  return { kind: "first", heldAt: local, status: "draft", reason: "", complaints: "", noComplaints: false,
    diseaseHistory: "", lifeHistory: "", examination: {}, diagnosis: "", diagnosisSaved: false,
    provisional: true, rationale: "", uncertainty: "", investigations: [], recommendations: "", nextVisit: "", worsening: "", diary: "" };
}

export function nextAfterTests(kind: Encounter["kind"], action?: TreatmentAction): VisitStep {
  return kind === "first" || action === "diagnosis" ? "diagnosis" : "decision";
}

export function completionErrors(visit: Encounter): string[] {
  const errors: string[] = [];
  if (!visit.diagnosis || !visit.diagnosisSaved) errors.push("Сохраните основной диагноз приёма.");
  if (!visit.risk) errors.push("Укажите актуальную оценку суицидального риска.");
  if (!visit.action) errors.push("Зафиксируйте решение по терапии.");
  if (visit.action && visit.action !== "continue" && !visit.therapyConfirmed) errors.push("Подтвердите обновлённую аптечку.");
  return errors;
}
