/** Triage pill of one diary entry; the reasons are the pill's title. */
import type { DoctorDiaryEntry } from "../../../../api/types";
import { Lv, TRIAGE } from "../../../../ui/levels";
import { triageReasons } from "./diary";

export function EntryTriage({ triage }: { triage: DoctorDiaryEntry["triage"] }) {
  if (!triage) return <Lv cls="lv-gry">Нет оценки</Lv>;
  const meta = TRIAGE[triage.level as keyof typeof TRIAGE];
  return (
    <span className={`lv ${meta?.cls ?? "lv-gry"}`} title={triageReasons(triage) || undefined}>
      {meta?.label ?? triage.label}
    </span>
  );
}
