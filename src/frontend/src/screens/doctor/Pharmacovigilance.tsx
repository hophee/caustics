import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { PatientSummary } from "../../api/types";
import { Dialog } from "../../ui/Dialog";
import { ErrorBox, Loading } from "../../ui/states";
import { formGroups, printableForm } from "./pharmacovigilanceForm";
import "./Pharmacovigilance.css";

export type Reaction = {
  adverse_event_id: string; patient_id: string; drug_name: string | null; symptom: string;
  onset_text: string | null; started_on: string | null; reported_at: string; details_complete: boolean;
};
type Form = { fields: Record<string, string> };
export function ReactionNotice({ events, patient }: { events: Reaction[]; patient: PatientSummary }) {
  const [selected, setSelected] = useState<Reaction | null>(null);
  return <>
    {events.map((event) => <div className="fn-notice" key={event.adverse_event_id}>
      <strong>Пациент сообщает об ухудшении после препарата</strong>
      <div className="sub">{new Date(event.reported_at).toLocaleString("ru-RU")}</div>
      {event.details_complete ? <>
        <div>{event.drug_name}</div>
        <div className="fn-quote">{event.symptom}</div>
        <div className="sub">Когда: {event.onset_text ?? event.started_on}</div>
      </> : <p>Подробности пока не заполнены.</p>}
      <button type="button" className="link-btn" onClick={() => setSelected(event)}>Сообщить о нежелательной реакции в Фармаконадзор</button>
    </div>)}
    {selected && <ReportDialog key={selected.adverse_event_id} event={selected} patient={patient} onClose={() => setSelected(null)} />}
  </>;
}

function ReportDialog({ event, patient, onClose }: { event: Reaction; patient: PatientSummary; onClose: () => void }) {
  const form = useQuery({ queryKey: ["fn-form", event.adverse_event_id], queryFn: () => api.get<Form>(`/adverse-events/${event.adverse_event_id}/form`) });
  return <Dialog name="pharmacovigilance" labelledBy="fn-title" onClose={onClose}>
    <h2 id="fn-title">Извещение о нежелательной реакции</h2>
    <p>Отправьте заполненную форму на e-mail: <a href="mailto:npr@roszdravnadzor.gov.ru">npr@roszdravnadzor.gov.ru</a> или онлайн на сайте: <a href="http://external.roszdravnadzor.ru/" target="_blank" rel="noreferrer">external.roszdravnadzor.ru</a>.</p>
    <p className="sub">Форма по приложенному извещению. Сохранение в приложении не отправляет её в Росздравнадзор.</p>
    <details><summary>Исходное сообщение пациента</summary><p>{event.drug_name}</p><div className="fn-quote">{event.details_complete ? event.symptom : "Подробности пока не заполнены"}</div><p>{event.onset_text}</p></details>
    {form.isPending && <Loading />}
    {form.isError && <ErrorBox error={form.error} onRetry={() => void form.refetch()} />}
    {form.data && <ReportEditor event={event} patient={patient} initial={form.data.fields} onClose={onClose} />}
  </Dialog>;
}

function ReportEditor({ event, patient, initial, onClose }: { event: Reaction; patient: PatientSummary; initial: Record<string, string>; onClose: () => void }) {
  const client = useQueryClient();
  const [fields, setFields] = useState<Record<string, string>>(() => ({
    type: "Первичное", patient_code: patient.patient_id, reporter: "Врач",
    drug0_name: event.drug_name ?? "", description: event.details_complete ? event.symptom : "",
    onset: event.onset_text ?? event.started_on ?? "", reported_on: new Date().toLocaleDateString("ru-RU"), ...initial,
  }));
  const [saved, setSaved] = useState(false);
  const [printError, setPrintError] = useState("");
  const save = useMutation({ mutationFn: () => api.put<Form>(`/adverse-events/${event.adverse_event_id}/form`, { fields }), onSuccess: (out) => {
    client.setQueryData(["fn-form", event.adverse_event_id], out);
    setSaved(true);
  } });
  const set = (key: string, value: string) => { setSaved(false); setFields((f) => ({ ...f, [key]: value })); };
  const print = () => {
    const missing = formGroups.flatMap((g) => g.fields).filter((f) => f.required && !fields[f.key]?.trim());
    if (missing.length) { setPrintError(`Заполните: ${missing.map((f) => f.label).join(", ")}`); return; }
    setPrintError("");
    const win = window.open("", "_blank");
    if (!win) { setPrintError("Разрешите открытие нового окна для печати."); return; }
    win.opener = null;
    save.mutate(undefined, { onSuccess: () => { win.document.write(printableForm(fields)); win.document.close(); win.focus(); win.print(); }, onError: () => win.close() });
  };
  return <form onSubmit={(e) => { e.preventDefault(); save.mutate(); }}>
    {formGroups.map((group) => <fieldset key={group.title}><legend>{group.title}</legend><div className="fn-fields">
      {group.fields.map((f) => <label className={`fld${f.multiline || f.multiple ? " fn-wide" : ""}`} key={f.key}>
        <span>{f.label}{f.required ? " *" : ""}</span>
        {f.multiple ? <span className="fn-checks">{f.choices?.map((choice) => <span key={choice}><input type="checkbox" checked={(fields[f.key] ?? "").split("; ").includes(choice)} onChange={(e) => {
          const values = (fields[f.key] ?? "").split("; ").filter(Boolean).filter((v) => v !== choice);
          set(f.key, [...values, ...(e.target.checked ? [choice] : [])].join("; "));
        }} /> {choice}</span>)}</span> : f.choices ? <select className="inp" value={fields[f.key] ?? ""} onChange={(e) => set(f.key, e.target.value)}><option value="">Не заполнено</option>{f.choices.map((c) => <option key={c}>{c}</option>)}</select> : f.multiline ? <textarea className="inp" rows={5} value={fields[f.key] ?? ""} maxLength={10000} onChange={(e) => set(f.key, e.target.value)} /> : <input className="inp" value={fields[f.key] ?? ""} maxLength={10000} onChange={(e) => set(f.key, e.target.value)} />}
      </label>)}
    </div></fieldset>)}
    {save.isError && <ErrorBox error={save.error} />}
    {printError && <p role="alert">{printError}</p>}
    {saved && <p role="status">Форма сохранена.</p>}
    <div className="fn-actions">
      <button type="submit" className="btn btn-p" disabled={save.isPending}>Сохранить форму</button>
      <button type="button" className="btn btn-s" disabled={save.isPending} onClick={print}>Печать / сохранить PDF</button>
      <button type="button" className="btn btn-t" onClick={onClose}>Закрыть</button>
    </div>
  </form>;
}
