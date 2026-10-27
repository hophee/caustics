/**
 * Shared queries. Key conventions (the live stream invalidates by prefix, see live/useLive.ts):
 *   doctor: ["overview"], ["alerts"], ["patient", patientId, …], ["kb", …]
 *   patient: ["me", …]
 */
import { useQuery } from "@tanstack/react-query";
import { api } from "./client";
import type { CaseOut, CurrentTherapy, Diagnosis, Me, PatientSummary, Profile } from "./types";

export const keys = {
  patient: (id: string) => ["patient", id] as const,
  summary: (id: string) => ["patient", id, "summary"] as const,
  profile: (id: string) => ["patient", id, "profile"] as const,
  activeCase: (id: string) => ["patient", id, "case"] as const,
  diagnoses: (id: string, caseId: string) => ["patient", id, "diagnoses", caseId] as const,
  therapy: (id: string) => ["patient", id, "therapy"] as const,
};

/** Name, diagnosis, case, diary days, open alerts and therapy of one patient (doctor only). */
export function usePatientSummary(patientId: string) {
  return useQuery({
    queryKey: keys.summary(patientId),
    queryFn: () => api.get<PatientSummary>(`/overview/patients/${patientId}`),
  });
}

export function useProfile(patientId: string) {
  return useQuery({
    queryKey: keys.profile(patientId),
    queryFn: () => api.get<Profile>(`/patients/${patientId}/profile`),
  });
}

/** The active case; `null` when the patient has none (404). */
export function useActiveCase(patientId: string) {
  return useQuery({
    queryKey: keys.activeCase(patientId),
    queryFn: async () => {
      try {
        return await api.get<CaseOut>(`/patients/${patientId}/cases/active`);
      } catch (error) {
        if ((error as { status?: number }).status === 404) return null;
        throw error;
      }
    },
  });
}

export function useDiagnoses(patientId: string, caseId: string | null | undefined) {
  return useQuery({
    queryKey: keys.diagnoses(patientId, caseId ?? ""),
    queryFn: () => api.get<Diagnosis[]>(`/cases/${caseId}/diagnoses`),
    enabled: Boolean(caseId),
  });
}

/** Current therapy; no items when the patient has no active case yet (409). */
export function useCurrentTherapy(patientId: string) {
  return useQuery({
    queryKey: keys.therapy(patientId),
    queryFn: async () => {
      try {
        return await api.get<CurrentTherapy>(`/patients/${patientId}/therapy/current`);
      } catch (error) {
        if ((error as { status?: number }).status === 409) return { items: [], base_token: "" };
        throw error;
      }
    },
  });
}

export type { Me };
