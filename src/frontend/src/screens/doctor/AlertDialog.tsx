/**
 * Board I2-Alert: one safety signal. Reason and the patient's answer, the signal path, the diary of
 * that day, then the doctor's acceptance and «Что сделано» (POST /alerts/{id}/handle). Nothing here
 * reaches the patient.
 */
import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import { usePatientSummary } from "../../api/queries";
import type { Alert, DoctorDiaryEntry, DoctorResult, Page, ScaleForm } from "../../api/types";
import { ageShort, when } from "../../lib/format";
import { CloseButton, Dialog } from "../../ui/Dialog";
import { AlertLevelPill, TriagePill } from "../../ui/levels";
import { errorText } from "../../ui/states";
import { RISK_LABEL, actionOptions, composeNote, patientWord, type Risk } from "./alertDecision";
import { alertSourceLabel, localDay, scaleCode, trackSteps } from "./alertTrack";
import { useAcceptAlert, useHandleAlert } from "./queries";
import "./AlertDialog.css";

export interface AlertDialogProps {
  alert: Alert;
  onClose: () => void;
}

function ScaleAnswer({ alert, sex }: { alert: Alert; sex: string | null | undefined }) {
  const code = scaleCode(alert.source) ?? "";
  const item = Number((alert.details as { item?: unknown }).item);
  const value = Number((alert.details as { value?: unknown }).value);
  const form = useQuery({
    queryKey: ["kb", "scale", code],
    queryFn: () => api.get<ScaleForm>(`/scales/${encodeURIComponent(code)}`),
    staleTime: Infinity,
  });
  const results = useQuery({
    queryKey: ["patient", alert.patient_id, "results"],
    queryFn: () => api.get<Page<DoctorResult>>(`/patients/${alert.patient_id}/results`, { limit: 200 }),
  });
  const question = form.data?.items.find((i) => i.id === item);
  const option = question?.options.find((o) => o.value === value);
  const result = results.data?.items.find((r) => r.response_id === alert.source_id);
  const band = (result?.band as { label?: string } | null | undefined)?.label;
  return (
    <>
      <h3>Ответ {patientWord(sex, "gen")}</h3>
      <blockquote>
        {question ? `«${question.text}»` : alert.reason}
        {option ? ` — ${option.label.toLocaleLowerCase("ru")}` : ""}
      </blockquote>
      <div className="sub">
        {code}, пункт {item} = {value}
        {result?.total != null && ` · сумма ${result.total}`}
        {band && ` · ${band.toLocaleLowerCase("ru")}`}
      </div>
    </>
  );
}

function SameDayDiary({ alert }: { alert: Alert }) {
  const day = localDay(alert.created_at);
  const diary = useQuery({
    queryKey: ["patient", alert.patient_id, "diary", day, day],
    queryFn: () => api.get<DoctorDiaryEntry[]>(`/patients/${alert.patient_id}/diary`, { since: day, until: day }),
  });
  const entries = diary.data ?? [];
  if (entries.length === 0) return null;
  return (
    <>
      <h3>Дневник в тот же день</h3>
      {entries.map((entry) => (
        <div className="entry" key={entry.revision_id}>
          <div className="eh">
            <b>{when(entry.saved_at)}</b>
            <TriagePill level={entry.triage?.level} />
          </div>
          {entry.text && <div>«{entry.text}»</div>}
          <span className="sub">
            мысли о самоповреждении:{" "}
            {entry.self_harm_thoughts === true ? "да" : entry.self_harm_thoughts === false ? "нет" : "Неизвестно"}
          </span>
        </div>
      ))}
    </>
  );
}

function Check({ on, onToggle, children }: { on: boolean; onToggle: () => void; children: React.ReactNode }) {
  return (
    <span
      className={`opt${on ? " on" : ""}`}
      role="checkbox"
      aria-checked={on}
      tabIndex={0}
      onClick={onToggle}
      onKeyDown={(event) => {
        if (event.key === " " || event.key === "Enter") {
          event.preventDefault();
          onToggle();
        }
      }}
    >
      <span className="box" />
      {children}
    </span>
  );
}

export function AlertDialog({ alert: initial, onClose }: AlertDialogProps) {
  const titleId = useId();
  const noteId = useId();
  const [alert, setAlert] = useState(initial);
  const summary = usePatientSummary(alert.patient_id);
  const patient = summary.data;
  const accept = useAcceptAlert();
  const handle = useHandleAlert();
  const [actions, setActions] = useState<string[]>([]);
  const [risk, setRisk] = useState<Risk | null>(null);
  const [note, setNote] = useState("");

  const sex = patient?.sex;
  const accepted = alert.status === "accepted" || alert.status === "handled";
  const handled = alert.status === "handled";
  const text = composeNote({ actions, risk, note }, sex);
  const steps = trackSteps(alert);
  const error = accept.error ?? handle.error;

  const toggle = (code: string) =>
    setActions((current) => (current.includes(code) ? current.filter((c) => c !== code) : [...current, code]));

  return (
    <Dialog name="alert" className="scrim" labelledBy={titleId} onClose={onClose}>
      <div className="mh">
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
            <AlertLevelPill level={alert.level} scenario={alert.scenario} />
            <span className="sub">
              {alertSourceLabel(alert.source)} · {when(alert.created_at)}
            </span>
          </div>
          <h1 id={titleId} style={{ fontSize: 22, lineHeight: "28px", marginTop: 6 }}>
            {patient?.full_name ?? "…"}
          </h1>
          {patient && (
            <div className="sub">
              {ageShort(patient.birth_date)}
              {patient.main_icd && ` · ${patient.main_icd}`}
            </div>
          )}
        </div>
        <CloseButton onClick={onClose} />
      </div>
      <div className="body">
        <div className="ctx">
          {scaleCode(alert.source) ? (
            <ScaleAnswer alert={alert} sex={sex} />
          ) : (
            <>
              <h3>Причина</h3>
              <blockquote>{alert.reason}</blockquote>
            </>
          )}
          <h3>Путь сигнала</h3>
          <div className="steps">
            {steps.map((step) => (
              <span key={step.status} className={`st${step.miss ? " no" : ""}${step.done ? "" : " off"}`}>
                <i />
                {step.label}
              </span>
            ))}
          </div>
          <SameDayDiary alert={alert} />
          <Link to={`/d/patients/${alert.patient_id}`} className="sub" onClick={onClose}>
            Открыть карту {patientWord(sex, "gen")}
          </Link>
        </div>
        <div className="dec">
          <h2>Что сделано</h2>
          {handled ? (
            <div className="done-note">{alert.handled_note}</div>
          ) : (
            <>
              <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                {actionOptions(sex).map((option) => (
                  <Check key={option.code} on={actions.includes(option.code)} onToggle={() => toggle(option.code)}>
                    {option.label}
                  </Check>
                ))}
              </div>
              <div className="fld">
                <span className="lbl" id={`${noteId}-risk`}>
                  Суицидальный риск
                </span>
                <span className="seg" role="radiogroup" aria-labelledby={`${noteId}-risk`} style={{ alignSelf: "flex-start" }}>
                  {(Object.keys(RISK_LABEL) as Risk[]).map((key) => (
                    <span
                      key={key}
                      role="radio"
                      aria-checked={risk === key}
                      tabIndex={0}
                      className={risk === key ? "on" : undefined}
                      onClick={() => setRisk(risk === key ? null : key)}
                      onKeyDown={(event) => {
                        if (event.key === " " || event.key === "Enter") {
                          event.preventDefault();
                          setRisk(risk === key ? null : key);
                        }
                      }}
                    >
                      {RISK_LABEL[key]}
                    </span>
                  ))}
                </span>
              </div>
              <div className="fld">
                <label htmlFor={noteId}>Заметка</label>
                <textarea id={noteId} className="inp" value={note} onChange={(event) => setNote(event.target.value)} />
              </div>
            </>
          )}
          {error != null && (
            <div role="alert" className="err">
              {errorText(error)}
            </div>
          )}
        </div>
      </div>
      <div className="mf">
        <button className="btn btn-t" type="button" onClick={onClose}>
          {handled ? "Закрыть" : "Отмена"}
        </button>
        {!accepted && (
          <button
            className="btn btn-t"
            type="button"
            disabled={accept.isPending}
            onClick={() => accept.mutate(alert.alert_id, { onSuccess: setAlert })}
          >
            Принять
          </button>
        )}
        {!handled && (
          <button
            className="btn btn-p"
            type="button"
            disabled={!accepted || !text || handle.isPending}
            onClick={() =>
              handle.mutate(
                { alertId: alert.alert_id, note: text },
                {
                  onSuccess: (updated) => {
                    setAlert(updated);
                    onClose();
                  },
                },
              )
            }
          >
            Записать решение
          </button>
        )}
      </div>
    </Dialog>
  );
}
