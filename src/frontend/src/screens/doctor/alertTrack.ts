/**
 * Alert delivery track (created → sent → delivered → accepted → handled) as view steps.
 * Only the last interaction carries a time; the first missing step after acceptance is shown as
 * overdue («не обработан, 18 ч»). An alert whose delivery retries ran out shows «не доставлен» as overdue.
 */
import type { Alert } from "../../api/types";
import { when } from "../../lib/format";

export const STATUSES = ["created", "sent", "delivered", "accepted", "handled"] as const;
export type AlertStatusName = (typeof STATUSES)[number];

const DONE: Record<AlertStatusName, string> = {
  created: "создан",
  sent: "отправлен",
  delivered: "доставлен",
  accepted: "принят",
  handled: "обработан",
};

const NOT_DONE: Record<AlertStatusName, string> = {
  created: "не создан",
  sent: "не отправлен",
  delivered: "не доставлен",
  accepted: "не принят",
  handled: "не обработан",
};

export interface TrackStep {
  status: AlertStatusName;
  done: boolean;
  /** Overdue: drawn dashed red. */
  miss: boolean;
  label: string;
}

/** Timestamps the payload may carry (sent_at / delivered_at are not in AlertOut yet). */
function stamp(alert: Alert, status: AlertStatusName): string | null {
  const extra = alert as Alert & { sent_at?: string | null; delivered_at?: string | null };
  switch (status) {
    case "created":
      return alert.created_at;
    case "sent":
      return extra.sent_at ?? null;
    case "delivered":
      return extra.delivered_at ?? null;
    case "accepted":
      return alert.accepted_at;
    case "handled":
      return alert.handled_at;
  }
}

/** «18 ч», «25 мин», «3 дн.» */
export function since(from: string | Date, now: Date): string {
  const minutes = Math.max(0, Math.floor((now.getTime() - new Date(from).getTime()) / 60_000));
  if (minutes < 60) return `${minutes} мин`;
  const hours = Math.floor(minutes / 60);
  if (hours < 48) return `${hours} ч`;
  return `${Math.floor(hours / 24)} дн.`;
}

/** The last step that happened and has a known time. */
export function lastInteraction(alert: Alert): { status: AlertStatusName; at: string } {
  const reached = STATUSES.indexOf(alert.status as AlertStatusName);
  for (let i = reached; i >= 0; i -= 1) {
    const status = STATUSES[i] as AlertStatusName;
    const at = stamp(alert, status);
    if (at) return { status, at };
  }
  return { status: "created", at: alert.created_at };
}

export function trackSteps(alert: Alert, now: Date = new Date()): TrackStep[] {
  const reached = STATUSES.indexOf(alert.status as AlertStatusName);
  const last = lastInteraction(alert);
  return STATUSES.map((status, index) => {
    const done = index <= reached;
    if (done) {
      const label = status === last.status ? `${DONE[status]} ${when(last.at, now)}` : DONE[status];
      return { status, done, miss: false, label };
    }
    const first = index === reached + 1;
    if (first && status === "handled" && alert.accepted_at) {
      return { status, done, miss: true, label: `${NOT_DONE[status]}, ${since(alert.accepted_at, now)}` };
    }
    if (first && status === "delivered" && alert.undelivered) {
      return { status, done, miss: true, label: NOT_DONE[status] };
    }
    return { status, done, miss: false, label: NOT_DONE[status] };
  });
}

/** Phone line: «принят сегодня, 15:48 · не обработан 18 ч». */
export function trackLine(alert: Alert, now: Date = new Date()): { last: string; next: string | null; miss: boolean } {
  const steps = trackSteps(alert, now);
  const last = lastInteraction(alert);
  const next = steps.find((s) => !s.done);
  return {
    last: `${DONE[last.status]} ${when(last.at, now)}`,
    next: next ? next.label.replace(", ", " ") : null,
    miss: next?.miss ?? false,
  };
}

/** «PHQ-9» from «scale:PHQ-9»; diary sources → «Дневник». */
export function alertSourceLabel(source: string): string {
  if (source.startsWith("scale:")) return source.slice("scale:".length);
  if (source.startsWith("diary")) return "Дневник";
  return source;
}

export function scaleCode(source: string): string | null {
  return source.startsWith("scale:") ? source.slice("scale:".length) : null;
}

/** Local calendar day (YYYY-MM-DD) of an instant. */
export function localDay(value: string | Date): string {
  const d = new Date(value);
  const pad = (n: number) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())}`;
}
