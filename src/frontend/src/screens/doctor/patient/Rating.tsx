import { Ellipsis } from "../../../ui/Ellipsis";
/** Clinician scale form (board I2-Rating): one item at a time, running sum, the computed result after saving. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useLocation, useParams } from "react-router";
import { api, newSubmissionId } from "../../../api/client";
import { useActiveCase, usePatientSummary } from "../../../api/queries";
import type { DoctorResult, ScaleForm } from "../../../api/types";
import { dayMonth, plural } from "../../../lib/format";
import { validScaleAnswer, visibleScaleItems } from "../../../lib/scaleForm";
import { useScreen } from "../../../ui/screen";
import { ErrorBox, Loading, errorText } from "../../../ui/states";
import { ProfileAside } from "./parts/ProfileAside";
import { usePatientId, useResults, useScaleForm } from "./parts/data";
import { runningSum } from "./parts/rating";
import { bandLabel, criticalText, scaleMax, score } from "./parts/scales";
import "./PatientCard.css";
import "./Rating.css";

export function Rating() {
  useScreen("rating");
  const patientId = usePatientId();
  const code = decodeURIComponent(useParams().scale ?? "");
  const summary = usePatientSummary(patientId).data;
  const form = useScaleForm(code);
  const activeCase = useActiveCase(patientId);
  const state: unknown = useLocation().state;
  const back =
    typeof state === "object" && state !== null && "back" in state && typeof state.back === "string" ? state.back : `/d/patients/${patientId}/scales`;

  return (
    <main className="sheet">
      <div className="sub patient-crumb">
        <Link to="/d/patients" style={{ color: "var(--muted)" }}>
          Пациенты
        </Link>{" "}
        /{" "}
        <Link to={`/d/patients/${patientId}/scales`} style={{ color: "var(--muted)" }}>
          <Ellipsis>{summary?.full_name ?? "…"}</Ellipsis>
        </Link>{" "}
        / {code}
      </div>
      <div className="pc">
        <ProfileAside patientId={patientId} />
        <div className="pmain">
          {form.isPending || activeCase.isPending ? (
            <Loading />
          ) : form.isError ? (
            <ErrorBox error={form.error} onRetry={() => void form.refetch()} />
          ) : !activeCase.data ? (
            <div className="card empty">Нет активного случая</div>
          ) : (
            <RatingForm key={form.data.code} patientId={patientId} caseId={activeCase.data.case_id} form={form.data} back={back} />
          )}
        </div>
      </div>
    </main>
  );
}

function RatingForm({ patientId, caseId, form, back }: { patientId: string; caseId: string; form: ScaleForm; back: string }) {
  const queryClient = useQueryClient();
  const [answers, setAnswers] = useState<Record<string, number>>({});
  const [index, setIndex] = useState(0);
  const [submissionId] = useState(newSubmissionId);
  const results = useResults(patientId).data?.items ?? [];
  const previous = results.filter((r) => r.scale_code === form.code).sort((a, b) => b.taken_at.localeCompare(a.taken_at))[0];
  const max = scaleMax(form);
  const items = visibleScaleItems(form, answers);
  const total = items.length;
  const step = Math.min(index, total - 1);
  const item = items[step];
  const done = items.filter((i) => validScaleAnswer(i, answers[i.id])).length;
  const save = useMutation({
    mutationFn: () => api.post<DoctorResult>(`/cases/${caseId}/ratings`, { submission_id: submissionId, scale_code: form.code, answers: Object.fromEntries(items.filter((i) => answers[i.id] !== undefined).map((i) => [i.id, answers[i.id]])) }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["patient", patientId] }),
  });
  const result = save.data;

  if (result) return <ResultView result={result} max={max} back={back} />;
  if (!item) return null;
  const choose = (value: number) => {
    setAnswers((a) => ({ ...a, [String(item.id)]: value }));
    if (step < total - 1) setIndex(step + 1);
  };

  return (
    <div className="cols">
      <div className="col">
        {form.notes?.map((note) => <p className="muted" key={note}>{note}</p>)}
        <div className="nav2">
          <button type="button" className="btn btn-t" disabled={step === 0} onClick={() => setIndex(step - 1)}>
            Назад
          </button>
          {step < total - 1 ? (
            <button type="button" className="btn btn-p" onClick={() => setIndex(step + 1)}>
              Далее
            </button>
          ) : (
            <button type="button" className="btn btn-p" disabled={done < total || save.isPending} onClick={() => save.mutate()}>
              Сохранить результат
            </button>
          )}
        </div>
        <section className="card">
          <div className="ch">
            <h2>
              {form.code} · пункт {step + 1} из {total}
            </h2>
            <span style={{ display: "flex", alignItems: "center", gap: 12 }}>
              <span className="sub">
                заполнено {done} из {total}
              </span>
              <Link className="btn btn-t" to={back}>
                Закрыть
              </Link>
            </span>
          </div>
          <div className="pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
            {"instructions" in form && typeof form.instructions === "string" && <p>{form.instructions}</p>}
            <div className="items" aria-label="Пункты шкалы" style={{ gridTemplateColumns: `repeat(${total},minmax(0,1fr))` }}>
              {items.map((it, i) => (
                <button
                  key={it.id}
                  type="button"
                  className={`${answers[String(it.id)] !== undefined ? "d" : ""}${i === step ? " c" : ""}`}
                  aria-label={`Пункт ${i + 1}`}
                  onClick={() => setIndex(i)}
                />
              ))}
            </div>
            <div style={{ font: "600 18px/24px 'Literata',serif" }} id="item-text">
              {item.text}
            </div>
            {item.hint && <p className="muted">{item.hint}</p>}
            {item.input === "number" ? (
              <input className="inp" type="number" aria-labelledby="item-text" min={item.min ?? 0} max={item.max ?? undefined} step={1} required
                value={answers[item.id] ?? ""} onChange={(e) => setAnswers((previous) => {
                  if (e.target.value === "") {
                    return Object.fromEntries(
                      Object.entries(previous).filter(([key]) => key !== String(item.id)),
                    );
                  }
                  return { ...previous, [item.id]: Number(e.target.value) };
                })} />
            ) : <div role="radiogroup" aria-labelledby="item-text" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {item.options.map((o) => {
                const on = answers[String(item.id)] === o.value;
                return (
                  <button key={o.value} type="button" role="radio" aria-checked={on} className={`opt${on ? " on" : ""}`} onClick={() => choose(o.value)}>
                    <b>{o.value}</b>
                    <span>{o.label !== String(o.value) ? o.label : ""}</span>
                  </button>
                );
              })}
            </div>}
          </div>
        </section>
        {save.isError && <span className="form-err">{errorText(save.error)}</span>}
      </div>
      <div className="col">
        <section className="card">
          <div className="ch">
            <h2>Итог</h2>
          </div>
          <div className="sum">
            <div>
              <span className="big">{form.simple_sum ? runningSum(form, answers) : "После сохранения"}</span>{" "}
              <span className="sub">
                {max !== null ? `из ${max} · ` : ""}после {done} {plural(done, "пункта", "пунктов", "пунктов")}
              </span>
            </div>
            <div className="kvr">
              <span className="muted">Предыдущий</span>
              <span>
                {previous ? [score(previous.total), dayMonth(previous.taken_at), bandLabel(previous)].filter(Boolean).join(" · ") : "—"}
              </span>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function ResultView({ result, max, back }: { result: DoctorResult; max: number | null; back: string }) {
  const critical = criticalText(result);
  return (
    <div className="cols">
      <div className="col">
        <section className="card">
          <div className="ch">
            <h2>{result.scale_code} · результат</h2>
            <span className="sub">{dayMonth(result.taken_at)}</span>
          </div>
          <div className="sum">
            <div>
              <span className="big">{score(result.total)}</span> <span className="sub">{[max !== null ? `из ${max}` : null, bandLabel(result)].filter(Boolean).join(" · ")}</span>
            </div>
            <div className="kvr">
              <span className="muted">Критические пункты</span>
              <span>
                {critical.length
                  ? critical.map((c) => (
                    <span key={c} className="lv lv-red" style={{ marginLeft: 4 }}>
                      {c}
                    </span>
                  ))
                  : "нет"}
              </span>
            </div>
            {(result.explanation ?? []).map((line) => (
              <div key={line} className="sub">
                {line}
              </div>
            ))}
          </div>
        </section>
        <div className="nav2">
          <span />
          <Link className="btn btn-p" to={back}>
            Закрыть
          </Link>
        </div>
      </div>
    </div>
  );
}
