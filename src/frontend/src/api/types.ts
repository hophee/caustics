/**
 * Types of API payloads. Typed endpoints reuse the generated schema; endpoints that return free-form
 * JSON on the backend (profile, basket check, selection, drug label, adherence…) are described here
 * from real responses (recorded with the backend test client).
 */
import type { Schemas } from "./client";

/** `pending`: registered, not linked to a doctor yet; `admin`: manages roles (D-088). */
export type Role = "doctor" | "patient" | "pending" | "admin";
export type AuthConfig = Schemas["AuthConfig"];
export type AdminUser = Schemas["UserOut"];
export type Me = Schemas["MeResponse"];
export type Page<T> = { items: T[]; total: number; offset: number; limit: number };

export type PatientSummary = Schemas["PatientSummaryOut"];
export type Alert = Schemas["AlertOut"];
export type AlertLevel = "urgent" | "high" | "planned";
export type UrgentScenario = Schemas["UrgentScenario"];
export type TriageLevel = "stable" | "attention" | "urgent";
export type CaseOut = Schemas["CaseOut"];
export type Diagnosis = Schemas["DiagnosisOut"];
export type Visit = Schemas["VisitDetailOut"];
export type Assignment = Schemas["AssignmentOut"];
export type DoctorResult = Schemas["DoctorResultOut"];
export type Series = Schemas["SeriesOut"];
export type DoctorDiaryEntry = Schemas["DoctorEntryOut"];
export type DiaryEntry = Schemas["EntryOut"];
export type Freshness = Schemas["FreshnessOut"];
export type Prescription = Schemas["PrescriptionOut"];
export type PrescriptionLine = Schemas["PrescriptionLineOut"];
export type Notification = Schemas["NotificationOut"];
export type Dose = Schemas["DoseOut"];
export type ReminderRule = Schemas["ReminderIn"];
export type DoctorReferrals = Schemas["DoctorReferralsOut"];
export type PatientReferral = Schemas["PatientReferralOut"];
export type PatientResponse = Schemas["PatientResponseOut"];

export type Tristate = "yes" | "no" | "unknown";

export interface Profile {
  birth_date: string;
  sex: "female" | "male";
  timezone: string;
  pregnant: Tristate;
  lactating: Tristate;
  substance_use: Tristate;
  suicide_risk: "low" | "high" | "unknown";
  diagnoses: string[];
  allergies: string[] | null;
  intolerant_excipients: string[] | null;
  past_intolerance: string[] | null;
  past_inefficacy: string[] | null;
  weight_kg: number | null;
  current_medications_reviewed_at: string | null;
}

export interface Source {
  document: string;
  ref: string;
  section: string | null;
  quote: string | null;
}

export type Severity = "contraindicated" | "caution" | "no_data";

export interface Warning {
  id: string;
  code: string;
  kind: string;
  severity: Severity;
  targets: { item_key: string; part: "item" | "dose" }[];
  message: string;
  sources: Source[];
  involves_new_item: boolean;
}

export interface CheckReport {
  warnings: Warning[];
  knowledge_version: string;
  boxes: { key: string; part: "item" | "dose"; severity: Severity }[];
}

export interface Intake {
  at: string;
  amount: number | string;
  unit?: string;
}

export interface Regimen {
  intakes: Intake[];
  start: string;
  duration_days: number;
  unit?: string;
}

/** Current regimen as stored (GET /therapy/current): a daily schedule or as-needed (prn). */
export type CurrentSchedule =
  | { kind: "daily"; intakes: { at: string; amount_mg: number }[] }
  | { kind: "prn"; dose_mg: number; [key: string]: unknown };

export interface CurrentItem {
  ref: string;
  label_id: string | null;
  substance: string | null;
  regimen: { schedule: CurrentSchedule; start: string; duration_days: number | null } | null;
  patient_reported: boolean;
}

export interface CurrentTherapy {
  items: CurrentItem[];
  base_token: string;
}

export interface DraftItem {
  key: string;
  label_id: string;
  origin: { kind: "search" } | { kind: "kr_suggestion"; row_id: string };
  regimen: Regimen;
}

export interface Draft {
  new_items: DraftItem[];
  stopped: string[];
}

export interface Fact<T> {
  value: T;
  quote: string | null;
  section: string | null;
}

export interface DrugLabel {
  id: string;
  display_name?: string;
  trade_name: string;
  form: string;
  atc: string | null;
  substances: string[];
  groups: string[];
  indications: Fact<string | null>;
  pregnancy: Fact<string>;
  lactation: Fact<string>;
  dose: {
    start_text: Fact<string | null>;
    max_text: Fact<string | null>;
    frequency_text: Fact<string | null>;
    max_daily_mg: string | number | null;
  };
  excipients: Fact<string[]>;
  caution_conditions: Fact<string | null>;
  named_interactions: Fact<string[]>;
  antidepressant_profile: string | null;
  needs_check: boolean;
  [key: string]: unknown;
}

export interface DrugSearchItem {
  label_id: string;
  display_name: string;
  trade_name: string;
  form: string;
  substances: string[];
  atc: string | null;
}

export interface KrTarget {
  kind: string;
  mnn?: string;
  off_label: boolean;
  evidence: string | null;
}

export interface KrRow {
  id: string;
  code: string;
  stage: string;
  condition: string;
  scheme: string;
  window: string;
  substances_text: string;
  targets: KrTarget[];
  evidence: string | null;
  source: string;
  quote: string;
}

export interface Candidate {
  label: DrugLabel;
  row: KrRow;
  off_label_in_kr: boolean;
  evidence: string | null;
}

export interface Selection {
  candidates: Candidate[];
  rows: KrRow[];
  advice: unknown[];
  restrictions: { rule: string; source: string; quote: string; condition: string }[];
  restrictions_to_clarify: unknown[];
}

export interface GuidelineDiagnosis {
  diagnosis: string;
  name: string;
}

/** `GET /guidelines/diagnoses`: a diagnosis with the КР and care standards that cover it (D-123). */
export interface GuidelineDocument {
  id: string;
  title: string;
  year: number;
  url: string;
}

export interface CareStandard {
  number: string;
  date: string;
  title: string;
  url: string;
}

export interface DiagnosisSources {
  code: string;
  name: string;
  guidelines: GuidelineDocument[];
  standards: CareStandard[];
}

export interface ScaleListItem {
  code: string;
  name: string;
  filled_by: "patient" | "clinician" | "patient_clinician";
  items: number;
}

export interface ScaleForm {
  code: string;
  name: string;
  version: number;
  filled_by: "patient" | "clinician" | "patient_clinician";
  notes?: string[];
  simple_sum?: boolean;
  max_total?: number | null;
  items: {
    id: number | string;
    text: string;
    hint?: string | null;
    section?: string | null;
    input?: "number" | null;
    min?: number;
    max?: number | null;
    show_if?: { match?: "any" | "all"; conditions: { item: number | string; equals: number }[] } | null;
    options: { value: number; label: string }[];
  }[];
}

export interface ComplaintCatalog {
  categories: { code: string; name: string; complaints: { code: string; name: string; description: string; critical: boolean }[] }[];
}

export interface AdherenceOptions {
  skip_reasons: { code: string; label: string }[];
  reminder_limits: { max_repeats: number; min_repeat_every_minutes: number; send_grace_minutes: number };
  reminder_defaults: ReminderRule;
  late_after_minutes: number;
}

export interface AdherenceSummary {
  expected: number;
  on_time: number;
  late: number;
  skipped: number;
  unmarked: number;
  percent: number | null;
  band: "green" | "yellow" | "red" | null;
  skip_reasons: Record<string, number>;
}

export interface Adherence {
  since: string;
  until: string;
  summary: AdherenceSummary;
  courses: { course_id: string; name: string; summary: AdherenceSummary }[];
  doses: { course_id: string; scheduled_at: string; state: string; name?: string }[];
  streaks: unknown[];
  skip_reason_labels: Record<string, string>;
  unmarked_counts_as_missed: boolean;
}
