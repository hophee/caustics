/** Queries of the doctor's overview, list and alert screens (keys follow src/api/queries.ts). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Alert, Page, PatientSummary } from "../../api/types";

/** The backend's page limit for `/overview/patients` and `/alerts`. */
export const MAX_LIMIT = 200;

export function useOverviewPatients(offset = 0, limit = MAX_LIMIT) {
  return useQuery({
    queryKey: ["overview", "patients", { offset, limit }],
    queryFn: () => api.get<Page<PatientSummary>>("/overview/patients", { days: 14, offset, limit }),
  });
}

export function useOpenAlerts() {
  return useQuery({
    queryKey: ["alerts", "open"],
    queryFn: () => api.get<Page<Alert>>("/alerts", { limit: MAX_LIMIT }),
  });
}

/** Refreshes everything an alert change touches. */
function useInvalidateAlert() {
  const queryClient = useQueryClient();
  return (alert: Alert) => {
    void queryClient.invalidateQueries({ queryKey: ["alerts"] });
    void queryClient.invalidateQueries({ queryKey: ["overview"] });
    void queryClient.invalidateQueries({ queryKey: ["patient", alert.patient_id] });
  };
}

export function useAcceptAlert() {
  const invalidate = useInvalidateAlert();
  return useMutation({
    mutationFn: (alertId: string) => api.post<Alert>(`/alerts/${alertId}/accept`),
    onSuccess: invalidate,
  });
}

export function useHandleAlert() {
  const invalidate = useInvalidateAlert();
  return useMutation({
    mutationFn: ({ alertId, note }: { alertId: string; note: string }) => api.post<Alert>(`/alerts/${alertId}/handle`, { note }),
    onSuccess: invalidate,
  });
}
