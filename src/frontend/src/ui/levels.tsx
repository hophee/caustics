/** Level pills shared by doctor screens: alert levels, diary triage, safety severity. */
import type { AlertLevel, Severity, TriageLevel, UrgentScenario } from "../api/types";

export const ALERT_LEVEL: Record<AlertLevel, { label: string; cls: string }> = {
  urgent: { label: "Срочно", cls: "lv-red" },
  high: { label: "Высокий", cls: "lv-amb" },
  planned: { label: "Плановый", cls: "lv-pln" },
};

/** What an urgent alert asks of the doctor (D-132): a structured answer vs. free text. */
export const URGENT_SCENARIO: Record<UrgentScenario, string> = {
  danger_now: "опасность сейчас",
  check_soon: "уточнить состояние",
};

export const TRIAGE: Record<TriageLevel, { label: string; cls: string; sq: string }> = {
  stable: { label: "Стабильно", cls: "lv-grn", sq: "s" },
  attention: { label: "Требует внимания", cls: "lv-amb", sq: "a" },
  urgent: { label: "Срочно", cls: "lv-red", sq: "u" },
};

export const SEVERITY: Record<Severity, { label: string; cls: string }> = {
  contraindicated: { label: "Противопоказано", cls: "lv-red" },
  caution: { label: "Осторожно", cls: "lv-amb" },
  no_data: { label: "Нет данных", cls: "lv-gry" },
};

export function Lv({ cls, children }: { cls: string; children: React.ReactNode }) {
  return <span className={`lv ${cls}`}>{children}</span>;
}

export function AlertLevelPill({ level, scenario }: { level: string; scenario?: UrgentScenario | null }) {
  const meta = ALERT_LEVEL[level as AlertLevel] ?? ALERT_LEVEL.planned;
  const detail = level === "urgent" && scenario ? URGENT_SCENARIO[scenario] : null;
  return <Lv cls={meta.cls}>{detail ? `${meta.label} — ${detail}` : meta.label}</Lv>;
}

export function TriagePill({ level }: { level: string | null | undefined }) {
  if (!level) return <Lv cls="lv-gry">Нет записей</Lv>;
  const meta = TRIAGE[level as TriageLevel];
  return meta ? <Lv cls={meta.cls}>{meta.label}</Lv> : <Lv cls="lv-gry">{level}</Lv>;
}

export function SeverityPill({ severity, children }: { severity: Severity; children?: React.ReactNode }) {
  const meta = SEVERITY[severity];
  return <Lv cls={meta.cls}>{children ?? meta.label}</Lv>;
}
