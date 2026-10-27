import type { CSSProperties, ReactNode } from "react";
import type { DiaryEntry } from "../../api/types";
import { dayMonth, longDay } from "../../lib/format";
import { type Period, eventTicks } from "../doctor/patient/parts/period";
import "./DailyTimeline.css";
import { PeriodViewport } from "../doctor/patient/parts/PeriodViewport";

export interface DayCell { tone: string; label: string }

export function binaryCell(value: boolean | null | undefined, yes: string, no: string): DayCell {
  return value == null ? { tone: "missing", label: "Нет ответа" } : { tone: value ? "yes" : "no", label: value ? yes : no };
}

export function moodCell(entry: DiaryEntry | undefined): DayCell {
  const moods = [
    { tone: "depressed", label: "Депрессивное" },
    { tone: "normal", label: "Нормальное" },
    { tone: "elevated", label: "Повышенное" },
  ];
  if (entry?.schema_version !== 2 || entry.mood == null) return { tone: "missing", label: "Нет ответа по новой шкале" };
  return moods[entry.mood] ?? { tone: "missing", label: "Нет ответа" };
}

/** All rows use the same latest days that fit the available width. */
export function DailyTimeline({ period, eventDays, children }: { period: Period; eventDays: string[]; children: (visible: Period) => ReactNode }) {
  return <PeriodViewport period={period} reserved={122}>{(visible) => <div className="daily-timeline" role="region" aria-label={`Динамика с ${longDay(visible.start)} по ${longDay(visible.end)}`}>
    <div className="timeline-inner" style={{ "--timeline-days": visible.days.length } as CSSProperties}>
      {children(visible)}
      <div className="timeline-row timeline-axis">
        <span>Дата</span>
        <div className="timeline-days">
          {eventTicks(visible, eventDays).map((tick) => <span key={tick.day} style={{ gridColumn: tick.col, gridRow: 1, justifySelf: tick.col === visible.days.length ? "end" : "start" }}>{dayMonth(tick.day)}</span>)}
        </div>
      </div>
    </div>
  </div>}</PeriodViewport>;
}

export function TimelineLane({ period, label, cell }: { period: Period; label: string; cell: (day: string) => DayCell }) {
  return <div className="timeline-row">
    <span className="timeline-label">{label}</span>
    <div className="timeline-days">
      {period.days.map((day) => {
        const value = cell(day);
        const description = `${longDay(day)} · ${label}: ${value.label}`;
        return <span key={day} className={`timeline-cell ${value.tone}`} role="img" aria-label={description} title={description}>
          {value.tone === "missing" ? "—" : null}
        </span>;
      })}
    </div>
  </div>;
}

export function DiarySignalLanes({ period, entries }: { period: Period; entries: DiaryEntry[] }) {
  const days = new Map(entries.map((entry) => [entry.day, entry]));
  return <>
    <TimelineLane period={period} label="Настроение" cell={(day) => moodCell(days.get(day))} />
    <TimelineLane period={period} label="Необычные проявления" cell={(day) => binaryCell(days.get(day)?.unusual_experiences, "Да", "Нет")} />
  </>;
}

export function DiarySignalLegend() {
  return <>
  <div className="timeline-legend"><strong>Настроение</strong>
    <span><i className="timeline-cell depressed" />Депрессивное</span>
    <span><i className="timeline-cell normal" />Нормальное</span>
    <span><i className="timeline-cell elevated" />Повышенное</span>
    <span><i className="timeline-cell missing">—</i>Нет ответа</span>
  </div>
  <div className="timeline-legend"><strong>Необычные проявления</strong>
    <span><i className="timeline-cell yes" />Да</span>
    <span><i className="timeline-cell no" />Нет</span>
    <span><i className="timeline-cell missing">—</i>Нет ответа</span>
  </div></>;
}
