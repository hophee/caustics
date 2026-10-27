/**
 * One questionnaire (boards P-Question → P-Result): an item per step, then only «Ответы сохранены».
 * The patient never sees a score, band or result (D-109); critical answers alert the doctor in the
 * background and change nothing on this screen.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Navigate, useParams } from "react-router";
import { api, newSubmissionId } from "../../api/client";
import type { Assignment, ScaleForm } from "../../api/types";
import { dayMonthLong } from "../../lib/format";
import { validScaleAnswer, visibleScaleItems } from "../../lib/scaleForm";
import { useScreen } from "../../ui/screen";
import { ErrorBox, Loading } from "../../ui/states";
import { localDay } from "./diaryFlow";
import { FlowScreen, SavedScreen } from "./Flow";
import { buildResponsePayload, isComplete, type Answers } from "./questionnaireFlow";
import { useAssignments, useScaleForm } from "./patientApi";
import "./patient.css";

export function Questionnaire() {
  const { assignmentId } = useParams();
  const assignments = useAssignments();
  // Keep the assignment once found: after submitting it is no longer due and leaves the list.
  const [assignment, setAssignment] = useState<Assignment | null>(null);
  const found = assignment ?? assignments.data?.find((a) => a.assignment_id === assignmentId) ?? null;
  if (found && found !== assignment) setAssignment(found);
  const scale = useScaleForm(found?.scale_code);

  if (assignments.isError) return <Pending error={assignments.error} />;
  if (!found) return assignments.isPending ? <Pending /> : <Navigate to="/p/forms" replace />;
  if (scale.isError) return <Pending error={scale.error} />;
  if (!scale.data) return <Pending />;
  return <QuestionSteps key={found.assignment_id} assignment={found} scale={scale.data} />;
}

function Pending({ error }: { error?: unknown }) {
  useScreen("p-question");
  return <main className="wrap narrow">{error ? <ErrorBox error={error} /> : <Loading />}</main>;
}

function QuestionSteps({ assignment, scale }: { assignment: Assignment; scale: ScaleForm }) {
  const [index, setIndex] = useState(0);
  const [answers, setAnswers] = useState<Answers>({});
  // One submission id per filling: retrying after a network error can't store the answers twice.
  const [submissionId] = useState(newSubmissionId);
  const [day] = useState(() => localDay());
  const queryClient = useQueryClient();

  const submit = useMutation({
    mutationFn: () => api.post("/me/responses", buildResponsePayload(scale, answers, submissionId, assignment.assignment_id)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["me", "assignments"] });
      void queryClient.invalidateQueries({ queryKey: ["me", "responses"] });
      void queryClient.invalidateQueries({ queryKey: ["me", "progress"] });
    },
  });

  if (submit.isSuccess) return <SavedScreen caption={`${scale.name} · ${dayMonthLong(day)}`} title="Ответы сохранены" />;

  const items = visibleScaleItems(scale, answers);
  const step = Math.min(index, items.length - 1);
  const item = items[step];
  if (!item) return null;
  const total = items.length;
  const chosen = answers[item.id];
  const last = step === total - 1;

  return (
    <FlowScreen screen="p-question" closeLabel="Закрыть анкету" closeTo="/p/forms">
      <div className="stepline">
        <h1 style={{ margin: 0, font: "500 14px/20px Geologica,sans-serif", color: "var(--muted)" }}>{scale.name}</h1>
        <span>
          {step + 1} из {total}
        </span>
      </div>
      <div className="prog" role="progressbar" aria-valuemin={1} aria-valuemax={total} aria-valuenow={step + 1} aria-label={`Вопрос ${step + 1} из ${total}`}>
        <i style={{ width: `${Math.round(((step + 1) / total) * 1000) / 10}%` }} />
      </div>
      <div className="questionnaire-nav">
        <button type="button" className="btn btn-s" disabled={step === 0} onClick={() => setIndex(step - 1)}>
          Назад
        </button>
        {last ? (
          <button type="button" className="btn btn-p" disabled={!isComplete(scale, answers) || submit.isPending} onClick={() => submit.mutate()}>
            {submit.isPending ? "Отправляем…" : "Сохранить результат"}
          </button>
        ) : (
          <button type="button" className="btn btn-p" disabled={!validScaleAnswer(item, chosen)} onClick={() => setIndex(step + 1)}>
            Далее
          </button>
        )}
      </div>
      <section className="card">
        <p className="q" id="question-text">{item.text}</p>
        {item.hint && <p className="muted">{item.hint}</p>}
        {item.input === "number" ? (
          <input className="inp" type="number" aria-labelledby="question-text" min={item.min ?? 0} max={item.max ?? undefined} step={1} required
            value={chosen ?? ""} onChange={(e) => setAnswers((previous) => {
              if (e.target.value === "") {
                return Object.fromEntries(
                  Object.entries(previous).filter(([key]) => key !== String(item.id)),
                );
              }
              return { ...previous, [item.id]: Number(e.target.value) };
            })} />
        ) : (
          <div className="opts" role="radiogroup" aria-label="Варианты ответа">
            {item.options.map((o) => (
              <button
                key={o.value}
                type="button"
                role="radio"
                aria-checked={chosen === o.value}
                className={`opt${chosen === o.value ? " on" : ""}`}
                onClick={() => setAnswers((a) => ({ ...a, [item.id]: o.value }))}
              >
                {o.label}
              </button>
            ))}
          </div>
        )}
      </section>
      {submit.isError && <ErrorBox error={submit.error} />}
    </FlowScreen>
  );
}
