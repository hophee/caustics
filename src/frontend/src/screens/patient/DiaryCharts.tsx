import { sleepDurationLabel } from "./diaryFlow";
import { DailyTimeline, DiarySignalLanes, DiarySignalLegend } from "./DailyTimeline";
import type { DiaryEntry } from "../../api/types";
import { HistoryChart } from "../doctor/patient/parts/Charts";
import type { Period } from "../doctor/patient/parts/period";

export function DiaryCharts({ entries, period, showSignals = true }: { entries: DiaryEntry[]; period: Period; showSignals?: boolean }) {
  const metrics = [
    { key: "anxiety", label: "Тревога (0–3)", max: 3 },
    { key: "irritability", label: "Раздражительность (0–3)", max: 3 },
    { key: "sleep_hours", label: "Сон (чч:мм)", max: 24 },
  ] as const;
  return <>
    {showSignals && <section className="card diary-signals">
      <h3>Настроение и необычные проявления</h3>
      <DailyTimeline period={period} eventDays={entries.map((entry) => entry.day)}>{(visible) => <DiarySignalLanes period={visible} entries={entries} />}</DailyTimeline>
      <DiarySignalLegend />
    </section>}
    <div className="patient-charts diary-charts">{metrics.map((m) => {
    const points = entries.filter((e) => e.schema_version === 2 && e[m.key] != null).map((e) => ({ day: e.day, value: Number(e[m.key]) }));
    return <section className="card" key={m.key}><h3>{m.label}</h3>
      {points.length ? <HistoryChart period={period} points={points} label={m.label} max={m.max} formatValue={m.key === "sleep_hours" ? sleepDurationLabel : undefined} /> : <p className="sub">Нет данных за этот период</p>}
    </section>;
  })}
    {entries.some((e) => e.schema_version !== 2) && (["mood", "anxiety", "energy", "sleep"] as const).map((key) => {
      const label = { mood: "Настроение", anxiety: "Тревога", energy: "Энергия", sleep: "Сон" }[key] + (key === "sleep" ? " (прежняя шкала: 0 — плохо, 1 — нормально, 2 — хорошо)" : " (прежняя шкала 0–10)");
      const points = entries.filter((e) => e.schema_version !== 2 && e[key] !== null).map((e) => ({ day: e.day, value: key === "sleep" ? {poor: 0, normal: 1, good: 2}[e.sleep!] : e[key]! }));
      return <section className="card" key={key}><h3>{label}</h3><HistoryChart period={period} points={points} label={label} max={key === "sleep" ? 2 : 10} /></section>;
    })}
  </div></>;
}
