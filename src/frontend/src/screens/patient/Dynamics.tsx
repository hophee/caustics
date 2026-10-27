import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import type { Series } from "../../api/types";
import { dayMonth } from "../../lib/format";
import { ErrorBox, Loading } from "../../ui/states";
import { HistoryChart } from "../doctor/patient/parts/Charts";
import { addDays, periodOf } from "../doctor/patient/parts/period";
import { seriesPoints } from "../doctor/patient/parts/scales";
import { DailyTimeline, TimelineLane, binaryCell } from "./DailyTimeline";
import { DiaryCharts } from "./DiaryCharts";
import { localDay } from "./diaryFlow";
import { useMyDiary, useMyDoses, useMyResponses, usePrescription } from "./patientApi";

export function PatientDynamics() {
  const today = localDay();
  const [start, setStart] = useState(addDays(today, -29));
  const [end, setEnd] = useState(today);
  const period = periodOf(start, end);
  const diary = useMyDiary(start, end);
  const doses = useMyDoses(start, end);
  const prescription = usePrescription();
  const results = useMyResponses("all");
  const progress = useQuery({ queryKey: ["me", "progress"], queryFn: () => api.get<Series[]>("/me/progress") });
  const error = diary.error ?? doses.error ?? prescription.error ?? results.error ?? progress.error;
  const entries = diary.data ?? [];
  const filled = new Set(entries.map((entry) => entry.day));
  const eventDays = [
    ...filled,
    ...(results.data?.items ?? []).map((result) => result.taken_on),
    ...(doses.data ?? []).map((dose) => dose.day),
    ...period.days.filter((day) => prescription.data?.some((line) => line.as_needed && line.start <= day && line.end >= day)),
  ];
  const series = progress.data ?? [];
  const adherence = period.days.flatMap((day) => {
    const planned = (doses.data ?? []).filter((dose) => dose.day === day && new Date(dose.scheduled_at).getTime() <= Date.now());
    return planned.length ? [{ day, value: 100 * planned.filter((dose) => dose.state === "on_time" || dose.state === "late").length / planned.length }] : [];
  });
  const loading = diary.isPending || doses.isPending || prescription.isPending || results.isPending || progress.isPending;
  return (
    <section className="patient-dynamics">
      <h2>Динамика состояния</h2>
      <div className="dynamics-toolbar">
        <label>С<input className="inp" type="date" value={start} min={addDays(end, -62)} max={end}
          onChange={(event) => { const day = event.target.value; if (day && day <= end && day >= addDays(end, -62)) setStart(day); }} /></label>
        <label>По<input className="inp" type="date" value={end} min={start} max={addDays(start, 62) < today ? addDays(start, 62) : today}
          onChange={(event) => { const day = event.target.value; if (day && day >= start && day <= today && day <= addDays(start, 62)) setEnd(day); }} /></label>
        <div className="dynamics-links">
          <Link to="/p/diary">Дневниковые записи</Link>
          <Link to="/p/forms">Результаты анкет</Link>
          <Link to="/p/visits">Приёмы</Link>
        </div>
      </div>
      <p className="sub">Период до двух месяцев. Наведите на отметку, чтобы увидеть дату и подробности.</p>
      {error && <ErrorBox error={error} />}
      {loading ? <Loading /> : !error && <>
        <section className="card">
          <h2>Линия курса · {dayMonth(start)} — {dayMonth(end)}</h2>
          <DailyTimeline period={period} eventDays={eventDays}>{(visible) => <>
            <TimelineLane period={visible} label="Дневники" cell={(day) => binaryCell(filled.has(day), "Заполнено", "Не заполнено")} />
            <TimelineLane period={visible} label="Шкалы" cell={(day) => {
              const items = results.data?.items.filter((result) => result.taken_on === day) ?? [];
              return items.length ? { tone: "scale-result", label: items.map((item) => `${item.scale_name}${item.total === null ? "" : `: ${item.total}`}`).join("; ") } : { tone: "empty", label: "Нет результатов" };
            }} />
            <TimelineLane period={visible} label="Терапия" cell={(day) => {
              const names = new Set((doses.data ?? []).filter((dose) => dose.day === day).map((dose) => dose.name));
              for (const line of prescription.data ?? []) {
                if (line.as_needed && line.start <= day && line.end >= day) names.add(`${line.trade_name} (по потребности)`);
              }
              return names.size ? { tone: "therapy", label: [...names].join(", ") } : { tone: "observation", label: "Нет запланированных доз" };
            }} />
          </>}
          </DailyTimeline>
          <div className="timeline-legend"><strong>Дневник</strong>
            <span><i className="timeline-cell yes" />Дневник заполнен</span>
            <span><i className="timeline-cell no" />Дневник не заполнен</span>
          </div>
          <div className="timeline-legend"><strong>Шкалы</strong>
            <span><i className="timeline-cell scale-result" />Результат шкалы</span>
          </div>
          <div className="timeline-legend"><strong>Терапия</strong>
            <span><i className="timeline-cell therapy" />План приёма препаратов</span>
            <span><i className="timeline-cell observation" />Нет запланированных доз</span>
          </div>
        </section>
        <div className="patient-charts">
          <section className="card">
            <h3>Приём лекарств</h3>
            <p className="sub">Доля доз, отмеченных принятыми, %</p>
            {adherence.length ? <HistoryChart period={period} label="Приём лекарств, %" max={100} points={adherence} /> : <p className="sub">Нет назначенных доз за этот период</p>}
          </section>
          {series.map((item) => {
            const points = seriesPoints(item, []).filter((point) => point.day >= start && point.day <= end);
            const label = [item.scale_code, item.subscale_code].filter(Boolean).join(" · ");
            return <section className="card" key={label}><h3>{label}</h3>
              {points.length ? <HistoryChart period={period} label={label} points={points} max={Math.max(1, ...points.map((point) => point.value))} scale /> : <p className="sub">Нет данных за этот период</p>}
            </section>;
          })}
        </div>
        <DiaryCharts entries={entries} period={period} />
      </>}
    </section>
  );
}
