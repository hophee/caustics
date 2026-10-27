/**
 * Queries of the doctor's patient card. Keys stay under ["patient", id, …] (and ["alerts"], ["kb"]),
 * so the live stream refreshes them.
 */
import { useQuery } from "@tanstack/react-query";
import { useParams } from "react-router";
import { ApiError, api } from "../../../../api/client";
import { useActiveCase } from "../../../../api/queries";
import { type Period, coursePeriod, localDay } from "./period";
import type {
  Alert,
  Assignment,
  ComplaintCatalog,
  DoctorDiaryEntry,
  DoctorReferrals,
  DoctorResult,
  Freshness,
  Page,
  Prescription,
  ScaleForm,
  ScaleListItem,
  Series,
  GuidelineDiagnosis,
  Visit,
} from "../../../../api/types";

/**
 * The case period shown on the course line and the charts (see `coursePeriod`): from the case
 * opening or its first visit, if earlier.
 */
export function useCasePeriod(patientId: string): Period | null {
  const activeCase = useActiveCase(patientId);
  const visits = useVisits(patientId, activeCase.data?.case_id);
  if (activeCase.isPending) return null;
  const today = localDay();
  return coursePeriod(activeCase.data ? localDay(activeCase.data.opened_at) : null, today, {
    earliest: (visits.data ?? []).map((v) => localDay(v.held_at)),
  });
}

/** The `:patientId` of the card's URL. */
export function usePatientId(): string {
  return useParams().patientId ?? "";
}

export const cardKeys = {
  diary: (id: string, since: string) => ["patient", id, "diary", since] as const,
  freshness: (id: string) => ["patient", id, "freshness"] as const,
  results: (id: string) => ["patient", id, "results"] as const,
  progress: (id: string) => ["patient", id, "progress"] as const,
  prescriptions: (id: string) => ["patient", id, "prescriptions"] as const,
  visits: (id: string, caseId: string) => ["patient", id, "visits", caseId] as const,
  assignments: (id: string, caseId: string) => ["patient", id, "assignments", caseId] as const,
  referrals: (id: string) => ["patient", id, "referrals"] as const,
  alerts: ["alerts", "all"] as const,
};

export function useDiary(patientId: string, since: string) {
  return useQuery({
    queryKey: cardKeys.diary(patientId, since),
    queryFn: () => api.get<DoctorDiaryEntry[]>(`/patients/${patientId}/diary`, { since }),
  });
}

export function useFreshness(patientId: string) {
  return useQuery({
    queryKey: cardKeys.freshness(patientId),
    queryFn: () => api.get<Freshness>(`/patients/${patientId}/freshness`, { window_days: 7 }),
  });
}

export function useResults(patientId: string) {
  return useQuery({
    queryKey: cardKeys.results(patientId),
    queryFn: () => api.get<Page<DoctorResult>>(`/patients/${patientId}/results`, { limit: 200 }),
  });
}

export function useProgress(patientId: string) {
  return useQuery({
    queryKey: cardKeys.progress(patientId),
    queryFn: () => api.get<Series[]>(`/patients/${patientId}/progress`),
  });
}

export function usePrescriptions(patientId: string) {
  return useQuery({
    queryKey: cardKeys.prescriptions(patientId),
    queryFn: () => api.get<Page<Prescription>>(`/patients/${patientId}/prescriptions`, { limit: 100 }),
  });
}

export function useVisits(patientId: string, caseId: string | null | undefined) {
  return useQuery({
    queryKey: cardKeys.visits(patientId, caseId ?? ""),
    queryFn: () => api.get<Visit[]>(`/cases/${caseId}/visits`),
    enabled: Boolean(caseId),
  });
}

export function useAssignments(patientId: string, caseId: string | null | undefined) {
  return useQuery({
    queryKey: cardKeys.assignments(patientId, caseId ?? ""),
    queryFn: () => api.get<Assignment[]>(`/cases/${caseId}/assignments`),
    enabled: Boolean(caseId),
  });
}

export function useReferrals(patientId: string) {
  return useQuery({
    queryKey: cardKeys.referrals(patientId),
    queryFn: () => api.get<DoctorReferrals>(`/patients/${patientId}/referrals`),
  });
}

/** The doctor's open alerts (GET /alerts returns only open ones); filter by patient at the use site. */
export function useOpenAlerts() {
  return useQuery({
    queryKey: cardKeys.alerts,
    queryFn: () => api.get<Page<Alert>>("/alerts", { limit: 200 }),
  });
}

export function useScales() {
  return useQuery({
    queryKey: ["kb", "scales"],
    queryFn: () => api.get<ScaleListItem[]>("/scales"),
    staleTime: Infinity,
  });
}

export function useScaleForm(code: string | undefined) {
  return useQuery({
    queryKey: ["kb", "scale", code ?? ""],
    queryFn: () => api.get<ScaleForm>(`/scales/${encodeURIComponent(code ?? "")}`),
    enabled: Boolean(code),
    staleTime: Infinity,
  });
}

export function useComplaints() {
  return useQuery({
    queryKey: ["kb", "complaints"],
    queryFn: () => api.get<ComplaintCatalog>("/complaints"),
    staleTime: Infinity,
  });
}

/** КР name of an ICD-10 code; null when the knowledge base has no guideline for it. */
export function useDiagnosisName(code: string | null | undefined) {
  return useQuery({
    queryKey: ["kb", "guideline-diagnosis", code ?? ""],
    queryFn: async () => {
      try {
        return (await api.get<GuidelineDiagnosis>("/guidelines/diagnosis", { diagnosis: code ?? "" })).name;
      } catch (error) {
        if (error instanceof ApiError && error.status === 404) return null;
        throw error;
      }
    },
    enabled: Boolean(code),
    staleTime: Infinity,
  });
}
