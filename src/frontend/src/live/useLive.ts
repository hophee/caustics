/**
 * Live notifications over SSE. The stream needs a single-use ticket (`POST /me/notifications/ticket`),
 * so the browser's own reconnect can't be used: on an error the stream is reopened with a new ticket
 * and `after_seq` = the last notification seen. Each event refreshes the queries it affects; a doctor's
 * browser also confirms technical delivery of alerts (`POST /alerts/{id}/delivered`).
 */
import { useQueryClient, type QueryClient } from "@tanstack/react-query";
import { useEffect } from "react";
import { api } from "../api/client";
import type { Notification, Role } from "../api/types";

const KINDS = [
  "alert",
  "adverse_event",
  "reported_drug",
  "prescription",
  "dose_reminder",
  "referral_recommendation",
  "referral",
] as const;

/** Query key prefixes refreshed by each notification kind. */
const AFFECTS: Record<string, string[][]> = {
  alert: [["alerts"], ["overview"], ["patient"]],
  adverse_event: [["reaction-todo"]],
  reported_drug: [["patient"]],
  prescription: [["me", "prescription"], ["me", "doses"]],
  dose_reminder: [["me", "doses"]],
  referral_recommendation: [["patient"]],
  referral: [["me", "referrals"], ["patient"]],
};

export function applyNotification(queryClient: QueryClient, role: Role, note: Notification): void {
  void queryClient.invalidateQueries({ queryKey: ["notifications"] });
  for (const key of AFFECTS[note.kind] ?? []) void queryClient.invalidateQueries({ queryKey: key });
  // A patient's own data all lives under ["me", …]; any notification may change it.
  if (role === "patient") void queryClient.invalidateQueries({ queryKey: ["me"] });
  const alertId = (note.payload as { alert_id?: string }).alert_id;
  if (role === "doctor" && note.kind === "alert" && alertId) {
    void api.post(`/alerts/${alertId}/delivered`).catch(() => undefined);
  }
}

export function useLive(role: Role | undefined): void {
  const queryClient = useQueryClient();

  useEffect(() => {
    if (!role) return;
    let closed = false;
    let source: EventSource | null = null;
    let retry: ReturnType<typeof setTimeout> | undefined;
    let lastSeq: number | null = null;
    let attempt = 0;

    const onEvent = (event: MessageEvent<string>) => {
      const note = JSON.parse(event.data) as Notification;
      lastSeq = Math.max(lastSeq ?? 0, note.seq);
      attempt = 0;
      applyNotification(queryClient, role, note);
    };

    const open = async () => {
      try {
        const { ticket } = await api.post<{ ticket: string }>("/me/notifications/ticket");
        if (closed) return;
        // First connection: no after_seq, the stream starts at the newest notification.
        const params = new URLSearchParams({ ticket });
        if (lastSeq !== null) params.set("after_seq", String(lastSeq));
        source = new EventSource(`/api/v1/me/notifications/stream?${params}`);
        for (const kind of KINDS) source.addEventListener(kind, onEvent);
        source.onerror = () => {
          source?.close();
          source = null;
          schedule();
        };
      } catch {
        schedule();
      }
    };

    const schedule = () => {
      if (closed) return;
      attempt += 1;
      retry = setTimeout(() => void open(), Math.min(30_000, 1000 * 2 ** Math.min(attempt, 5)));
    };

    void open();
    return () => {
      closed = true;
      clearTimeout(retry);
      source?.close();
    };
  }, [role, queryClient]);
}
