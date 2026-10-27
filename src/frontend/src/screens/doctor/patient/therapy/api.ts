/**
 * Queries of the therapy screens. Keys follow the shared convention (`["patient", id, …]`, `["kb", …]`)
 * so live notifications invalidate them.
 *
 * Local types fix shapes that differ from `src/api/types.ts` (recorded from the dev API):
 * - current therapy items carry `ref` and `patient_reported`, not `course_id`/`trade_name`/`form`;
 * - prescription items carry `provenance` and `suggested_dose_text`;
 * - adherence doses carry `state`, not `status`;
 * - draft origin kind is `kr_suggestion`, not `suggestion`.
 */
import type { SelectionAnswers } from "./SelectionControls";
import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { api } from "../../../../api/client";
import type { AdherenceSummary, CheckReport, DrugLabel, KrRow, Selection, Warning } from "../../../../api/types";
import type { DraftOut } from "./model";

export interface CurrentRef {
  ref: string;
  label_id: string | null;
  substance: string | null;
  regimen: unknown;
  patient_reported: boolean;
}

export interface CurrentTherapyOut {
  items: CurrentRef[];
  base_token: string;
}

export interface Provenance {
  row: KrRow | null;
  kr_status: "recommended" | "unverified" | "not_mentioned";
  label_indicated: "yes" | "no" | "unknown";
  off_label_in_kr: boolean;
}

export interface PrescribedItem {
  course_id: string;
  label_id: string | null;
  substance: string | null;
  trade_name: string;
  form: string;
  regimen: unknown;
  is_new: boolean;
  provenance: Provenance | null;
  suggested_dose_text: string | null;
}

export interface PrescriptionVersion {
  prescription_id: string;
  version: number;
  confirmed_at: string;
  knowledge_version: string;
  items: PrescribedItem[];
  stopped_course_ids: string[];
  warnings: Warning[];
  justifications: { warning_id: string; text: string }[];
}

export interface AdherenceOut {
  since: string;
  until: string;
  summary: AdherenceSummary;
  courses: { course_id: string; name: string; summary: AdherenceSummary }[];
  doses: { course_id: string; name: string; scheduled_at: string; state: string }[];
  skip_reason_labels: Record<string, string>;
  unmarked_counts_as_missed: boolean;
}

export const therapyKeys = {
  current: (id: string) => ["patient", id, "therapy"] as const,
  prescriptions: (id: string, limit: number) => ["patient", id, "prescriptions", limit] as const,
  adherence: (id: string) => ["patient", id, "adherence"] as const,
  check: (id: string, draft: DraftOut, base: string) => ["patient", id, "basket-check", base, draft] as const,
  selection: (id: string) => ["patient", id, "selection"] as const,
  label: (labelId: string) => ["kb", "drug", labelId] as const,
  search: (q: string) => ["kb", "drug-search", q] as const,
};

export function useCurrent(patientId: string) {
  return useQuery({
    queryKey: therapyKeys.current(patientId),
    queryFn: () => api.get<CurrentTherapyOut>(`/patients/${patientId}/therapy/current`),
  });
}

export function usePrescriptions(patientId: string, limit = 20) {
  return useQuery({
    queryKey: therapyKeys.prescriptions(patientId, limit),
    queryFn: () =>
      api.get<{ items: PrescriptionVersion[]; total: number }>(`/patients/${patientId}/prescriptions`, { limit }),
    placeholderData: keepPreviousData,
  });
}

export function useAdherence(patientId: string) {
  return useQuery({
    queryKey: therapyKeys.adherence(patientId),
    queryFn: () => api.get<AdherenceOut>(`/patients/${patientId}/adherence`),
  });
}

/** Live check of a draft; re-runs whenever the payload changes (keeps the last report meanwhile). */
export function useCheck(patientId: string, draft: DraftOut, base: string | undefined, enabled = true) {
  return useQuery({
    queryKey: therapyKeys.check(patientId, draft, base ?? ""),
    queryFn: () => api.post<CheckReport>(`/patients/${patientId}/basket/check`, draft),
    placeholderData: keepPreviousData,
    enabled: enabled && base !== undefined,
    staleTime: 0,
  });
}

/** Every КР row of the main diagnosis with its labels; no therapy stage / line (D-122). */
export function useSelection(patientId: string, answers: SelectionAnswers = { flags: [] }) {
  const params = new URLSearchParams();
  if (answers.choice !== undefined) params.set("choice", answers.choice);
  answers.flags.forEach((flag) => params.append("flag", flag));
  return useQuery({
    queryKey: [...therapyKeys.selection(patientId), answers],
    queryFn: () => api.get<Selection>(`/patients/${patientId}/suggestions?${params}`),
    staleTime: 60_000,
  });
}

export function useLabel(labelId: string | null | undefined) {
  return useQuery({
    queryKey: therapyKeys.label(labelId ?? ""),
    queryFn: () => api.get<DrugLabel>(`/drugs/${labelId}`),
    enabled: Boolean(labelId),
    staleTime: Infinity,
  });
}

export interface SearchHit {
  label_id: string;
  display_name: string;
  trade_name: string;
  form: string;
  substances: string[];
  atc: string | null;
}

export function useDrugSearch(q: string, filters: {atc?: string; form?: string; mnn?: string; offset?: number} = {}) {
  const query = q.trim();
  return useQuery({
    queryKey: [...therapyKeys.search(query), filters],
    queryFn: () => api.get<{ items: SearchHit[]; total: number }>("/drugs", { q: query, limit: 50, ...filters }),
    enabled: true,
    placeholderData: keepPreviousData,
    staleTime: 60_000,
  });
}
