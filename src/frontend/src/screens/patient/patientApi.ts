/**
 * Patient queries (keys under ["me", …], refreshed by the live stream) and the shared catalogues
 * (scales, complaints, adherence options) they need.
 */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef } from "react";
import { api, newSubmissionId } from "../../api/client";
import type {
  AdherenceOptions,
  Assignment,
  ComplaintCatalog,
  DiaryEntry,
  Dose,
  Page,
  PatientResponse,
  PrescriptionLine,
  ReminderRule,
  ScaleForm,
  ScaleListItem,
} from "../../api/types";

export const meKeys = {
  diary: (since: string, until: string) => ["me", "diary", since, until] as const,
  doses: (since: string, until: string) => ["me", "doses", since, until] as const,
  assignments: ["me", "assignments"] as const,
  responses: (limit: number | "all") => ["me", "responses", limit] as const,
  prescription: ["me", "prescription"] as const,
  reminders: ["me", "reminders"] as const,
  referrals: ["me", "referrals"] as const,
  // The live stream refreshes ["notifications"] on new events.
  notifications: ["notifications", "me"] as const,
};

export function useMyDiary(since: string, until: string) {
  return useQuery({
    queryKey: meKeys.diary(since, until),
    queryFn: () => api.get<DiaryEntry[]>("/me/diary", { since, until }),
  });
}

export function useMyDoses(since: string, until: string) {
  return useQuery({
    queryKey: meKeys.doses(since, until),
    queryFn: () => api.get<Dose[]>("/me/doses", { since, until }),
  });
}

export function useAssignments() {
  return useQuery({ queryKey: meKeys.assignments, queryFn: () => api.get<Assignment[]>("/me/assignments") });
}

export function useMyResponses(limit: number | "all") {
  return useQuery({
    queryKey: meKeys.responses(limit),
    queryFn: async () => {
      const first = await api.get<Page<PatientResponse>>("/me/responses", { limit: limit === "all" ? 200 : Math.min(limit, 200) });
      const target = limit === "all" ? first.total : Math.min(limit, first.total);
      const items = [...first.items];
      while (items.length < target) {
        const page = await api.get<Page<PatientResponse>>("/me/responses", { offset: items.length, limit: Math.min(200, target - items.length) });
        if (!page.items.length) break;
        items.push(...page.items);
      }
      return { ...first, items, limit: limit === "all" ? items.length : limit };
    },
    placeholderData: (previous) => previous,
  });
}

export function usePrescription() {
  return useQuery({ queryKey: meKeys.prescription, queryFn: () => api.get<PrescriptionLine[]>("/me/prescription") });
}

export function useReminders() {
  return useQuery({ queryKey: meKeys.reminders, queryFn: () => api.get<ReminderRule>("/me/reminders") });
}

export function useScales() {
  return useQuery({ queryKey: ["catalog", "scales"], queryFn: () => api.get<ScaleListItem[]>("/scales"), staleTime: Infinity });
}

export function useScaleForm(code: string | undefined) {
  return useQuery({
    queryKey: ["catalog", "scale", code],
    queryFn: () => api.get<ScaleForm>(`/scales/${encodeURIComponent(code ?? "")}`),
    enabled: Boolean(code),
    staleTime: Infinity,
  });
}

export function useComplaints() {
  return useQuery({ queryKey: ["catalog", "complaints"], queryFn: () => api.get<ComplaintCatalog>("/complaints"), staleTime: Infinity });
}

export function useAdherenceOptions() {
  return useQuery({ queryKey: ["catalog", "adherence"], queryFn: () => api.get<AdherenceOptions>("/adherence/options"), staleTime: Infinity });
}

export interface MarkInput {
  dose: Dose;
  status: "taken" | "skipped";
  reason?: string;
}

/** «Принял» / «Пропустил». One submission id per attempt, kept across retries of the same mark. */
export function useMarkDose() {
  const queryClient = useQueryClient();
  const pending = useRef(new Map<string, string>());
  return useMutation({
    mutationFn: ({ dose, status, reason }: MarkInput) => {
      const key = `${dose.course_id}|${dose.scheduled_at}|${status}|${reason ?? ""}`;
      let id = pending.current.get(key);
      if (!id) {
        id = newSubmissionId();
        pending.current.set(key, id);
      }
      return api
        .post<Dose>("/me/doses/marks", {
          submission_id: id,
          course_id: dose.course_id,
          scheduled_at: dose.scheduled_at,
          status,
          reason: status === "skipped" ? reason : null,
        })
        .then((out) => {
          pending.current.delete(key);
          return out;
        });
    },
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["me", "doses"] }),
  });
}
