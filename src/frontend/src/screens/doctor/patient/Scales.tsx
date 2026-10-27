/** «Шкалы» tab (board I2-Scales): one chart per scale, assigned questionnaires, all results. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api } from "../../../api/client";
import { useActiveCase } from "../../../api/queries";
import type { Assignment, DoctorResult, Series } from "../../../api/types";
import { dayMonth } from "../../../lib/format";
import { useScreen } from "../../../ui/screen";
import { ErrorBox, Loading, errorText } from "../../../ui/states";
import { Axis, ScaleChart, type TherapyOverlay } from "./parts/Charts";
import { useAssignments, useCasePeriod, usePatientId, useProgress, useResults, useScaleForm, useScales } from "./parts/data";
import { type Period, addDays, localDay, periodOf } from "./parts/period";
import { assignmentNext, assignmentSchedule, bandLabel, criticalText, scaleMax, score, seriesPoints } from "./parts/scales";
import { DateInput } from "../../../ui/DateInput";
import "./Scales.css";

export function DoctorScales() {
  useScreen("scales");
  const patientId = usePatientId();
  const activeCase = useActiveCase(patientId).data;
  const casePeriod = useCasePeriod(patientId);
  const progress = useProgress(patientId);
  const results = useResults(patientId);
  const [assigning, setAssigning] = useState(false);
  const [picking, setPicking] = useState(false);
  const clinician = (useScales().data ?? []).filter((s) => s.filled_by !== "patient");

  if (!casePeriod || progress.isPending || results.isPending) return <Loading />;
  if (progress.isError) return <ErrorBox error={progress.error} onRetry={() => void progress.refetch()} />;
  if (results.isError) return <ErrorBox error={results.error} onRetry={() => void results.refetch()} />;
  const items = results.data.items;
  const firstDay = [...progress.data.flatMap((s) => s.points.map((p) => String(p.taken_on ?? "")))].filter(Boolean).sort()[0];
  const period = firstDay && firstDay < casePeriod.start ? periodOf(firstDay, casePeriod.end) : casePeriod;

  return (
    <>
      <div className="acts">
        <button type="button" className="btn btn-t" onClick={() => setAssigning((v) => !v)} disabled={!activeCase} aria-expanded={assigning}>
          Назначить анкету
        </button>
        <div style={{ position: "relative" }}>
          <button type="button" className="btn btn-p" onClick={() => setPicking((v) => !v)} disabled={!activeCase} aria-expanded={picking}>
            Заполнение опросника
          </button>
          {picking && (
            <div className="card scale-menu" role="menu">
              {clinician.map((s) => (
                <Link key={s.code} role="menuitem" to={`/d/patients/${patientId}/rating/${encodeURIComponent(s.code)}`}>
                  {s.name}
                </Link>
              ))}
            </div>
          )}
        </div>
      </div>
      {assigning && activeCase && <AssignForm patientId={patientId} caseId={activeCase.case_id} onDone={() => setAssigning(false)} />}
      {progress.data.length > 0 && (
        <div className="charts">
          {progress.data.map((s) => (
            <ScaleCard key={`${s.scale_code}/${s.subscale_code ?? ""}`} series={s} results={items} period={period} />
          ))}
        </div>
      )}
      {activeCase && <AssignmentsCard patientId={patientId} caseId={activeCase.case_id} />}
      <section className="card">
        <div className="ch">
          <h2>Результаты</h2>
          <span className="sub">{results.data.total}</span>
        </div>
        {items.length === 0 ? (
          <div className="empty">Результатов нет</div>
        ) : (
          <div className="scroll">
            <table>
              <thead>
                <tr>
                  <th className="num">Дата</th>
                  <th>Шкала</th>
                  <th className="num">Балл</th>
                  <th>Уровень</th>
                  <th>Критичные пункты</th>
                </tr>
              </thead>
              <tbody>
                {[...items]
                  .sort((a, b) => b.taken_at.localeCompare(a.taken_at))
                  .map((r) => (
                    <ResultRow key={r.response_id} result={r} />
                  ))}
              </tbody>
            </table>
          </div>
        )}
      </section>
    </>
  );
}

function ResultRow({ result }: { result: DoctorResult }) {
  const critical = criticalText(result);
  return (
    <tr title={(result.explanation ?? []).join("\n") || undefined}>
      <td className="num">{dayMonth(result.taken_at)}</td>
      <td style={{ fontWeight: 500 }}>{result.scale_code}</td>
      <td className="num">{score(result.total)}</td>
      <td className="wrap">{bandLabel(result) ?? (result.status === "complete" ? "—" : "не завершена")}</td>
      {critical.length ? (
        <td>
          {critical.map((c) => (
            <span key={c} className="lv lv-red" style={{ marginRight: 4 }}>
              {c}
            </span>
          ))}
        </td>
      ) : (
        <td className="muted">—</td>
      )}
    </tr>
  );
}

function niceCeil(value: number): number {
  const step = value <= 10 ? 2 : value <= 30 ? 5 : 10;
  return Math.max(step, Math.ceil(value / step) * step);
}

export function ScaleCard({ series, results, period, therapy }: { series: Series; results: DoctorResult[]; period: Period; therapy?: TherapyOverlay }) {
  const form = useScaleForm(series.scale_code).data;
  const points = seriesPoints(series, results);
  const last = points.at(-1);
  const formMax = series.subscale_code === null ? scaleMax(form) : null;
  const max = formMax ?? niceCeil(Math.max(0, ...points.map((p) => p.value)));
  const latest = [...results].filter((r) => r.scale_code === series.scale_code).sort((a, b) => b.taken_at.localeCompare(a.taken_at))[0];
  const band = series.subscale_code === null ? bandLabel(latest) : null;
  const title = series.subscale_code ? `${series.scale_code} · ${series.subscale_code}` : series.scale_code;
  return (
    <section className="card mini">
      <div className="mh">
        <b>{title}</b>
        <span>
          <span style={{ fontSize: 20, fontWeight: 500 }}>{score(last?.value)}</span>{" "}
          <span className="sub">{[formMax !== null ? `из ${formMax}` : null, band].filter(Boolean).join(" · ")}</span>
        </span>
      </div>
      {therapy?.baseline !== undefined && <p className="sub">Исходно: {therapy.baseline}; начало терапии: {therapy.start}; срок оценки: {therapy.from}–{therapy.until}</p>}
      <div className="chart-scroll" role="region" aria-label={title} tabIndex={0}><div style={{ minWidth: period.days.length * 24 }}>
      <ScaleChart period={period} points={points} max={max} code={title} therapy={therapy} />
      <Axis period={period} eventDays={points.map((point) => point.day)} scale />
      </div></div>
    </section>
  );
}

export function AssignmentsCard({ patientId, caseId }: { patientId: string; caseId: string }) {
  const assignments = useAssignments(patientId, caseId);
  const scales = useScales().data ?? [];
  const today = localDay();
  const kind = (code: string) => (scales.find((s) => s.code === code)?.filled_by === "clinician" ? "шкала врача" : "самоопросник");
  return (
    <section className="card">
      <div className="ch">
        <h2>Назначенные анкеты</h2>
      </div>
      {assignments.isPending ? (
        <div className="pad">
          <Loading />
        </div>
      ) : assignments.isError ? (
        <div className="pad">
          <ErrorBox error={assignments.error} />
        </div>
      ) : assignments.data.length === 0 ? (
        <div className="empty">Анкеты не назначены</div>
      ) : (
        assignments.data.map((a: Assignment) => (
          <div key={a.assignment_id} className="as">
            <div>
              <div style={{ fontWeight: 500 }}>
                {a.scale_code} · {kind(a.scale_code)}
              </div>
              <div className="sub">{assignmentSchedule(a)}</div>
            </div>
            <span className="sub">{assignmentNext(a, today)}</span>
            <span className="lv lv-pln">назначена</span>
          </div>
        ))
      )}
    </section>
  );
}

export function AssignForm({ patientId, caseId, onDone }: { patientId: string; caseId: string; onDone: () => void }) {
  const queryClient = useQueryClient();
  const scales = (useScales().data ?? []).filter((s) => s.filled_by !== "clinician");
  const [code, setCode] = useState("");
  const [repeat, setRepeat] = useState(false);
  const [every, setEvery] = useState("14");
  const [start, setStart] = useState(localDay());
  const [due, setDue] = useState("3");
  const assigned = useAssignments(patientId, caseId);
  const duplicate = assigned.data?.some((a) => a.scale_code === code && (a.mode === "repeat" || a.starts_on >= localDay()));
  const save = useMutation({
    mutationFn: () =>
      api.post<Assignment>(`/cases/${caseId}/assignments`, {
        scale_code: code,
        mode: repeat ? "repeat" : "once",
        every_days: repeat ? Number(every) : null,
        starts_on: start,
        due_days: Number(due),
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["patient", patientId, "assignments"] });
      onDone();
    },
  });
  const interval = Number(every);
  const duration = Number(due);
  const valid = code && start && Number.isInteger(duration) && duration >= 1
    && (!repeat || (Number.isInteger(interval) && interval >= 1 && duration % interval === 0)) && !duplicate;
  return (
    <form
      className="card pad assign"
      onSubmit={(event) => {
        event.preventDefault();
        if (valid) save.mutate();
      }}
    >
      {duplicate && <p role="alert">Эта шкала уже назначена. Существующее задание показано ниже.</p>}
      <div className="fld">
        <label htmlFor="as-scale">Анкета</label>
        <select id="as-scale" className="inp" value={code} onChange={(e) => setCode(e.target.value)} required>
          <option value="" disabled>
            Выберите
          </option>
          {scales.map((s) => (
            <option key={s.code} value={s.code}>
              {s.name}
            </option>
          ))}
        </select>
      </div>
      <div className="fld">
        <span className="lbl">Режим</span>
        <span className="seg">
          <button type="button" className={!repeat ? "on" : ""} aria-pressed={!repeat} onClick={() => {
            if (repeat) setDue("3");
            setRepeat(false);
          }}>
            Разово
          </button>
          <button type="button" className={repeat ? "on" : ""} aria-pressed={repeat} onClick={() => {
            if (!repeat) setDue(String(interval * 2));
            setRepeat(true);
          }}>
            Повторять
          </button>
        </span>
      </div>
      {repeat && (
        <div className="fld">
          <label htmlFor="as-every">Каждые, дней</label>
          <input id="as-every" className="inp" type="number" min={1} step={1} required value={every} onChange={(e) => {
            setEvery(e.target.value);
            setDue(e.target.value ? String(Number(e.target.value) * 2) : "");
          }} />
        </div>
      )}
      <div className="fld">
        <label htmlFor="as-start">Начало</label>
        <DateInput id="as-start" min={addDays(localDay(), -30)} value={start} onChange={setStart} />
      </div>
      <div className="fld">
        <label htmlFor="as-due">Срок, дней</label>
        <input id="as-due" className="inp" type="number" min={repeat ? interval || 1 : 1} step={repeat ? interval || 1 : 1} required value={due} onChange={(e) => setDue(e.target.value)} />
      </div>
      {save.isError && <span className="form-err">{errorText(save.error)}</span>}
      <div className="acts" style={{ gridColumn: "1 / -1" }}>
        <button type="button" className="btn btn-t" onClick={onDone}>
          Отменить
        </button>
        <button type="submit" className="btn btn-p" disabled={!valid || save.isPending}>
          Назначить
        </button>
      </div>
    </form>
  );
}
