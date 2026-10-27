import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api, newSubmissionId } from "../../api/client";
import { ErrorBox } from "../../ui/states";
import { usePrescription } from "./patientApi";

type Saved = { adverse_event_id: string; details_complete: boolean };

export function ReactionQuestion({ tradeName = "" }: { tradeName?: string }) {
  const id = useId();
  const client = useQueryClient();
  const prescription = usePrescription();
  const pending = useQuery({ queryKey: ["my-reaction-pending"], queryFn: () => api.get<Saved[]>("/me/reaction-signals/pending") });
  const [submission, setSubmission] = useState(newSubmissionId);
  const [eventId, setEventId] = useState<string | null>(null);
  const [answer, setAnswer] = useState<"yes" | "no" | null>(null);
  const [name, setName] = useState(tradeName);
  const [description, setDescription] = useState("");
  const [onset, setOnset] = useState("");
  const [saved, setSaved] = useState(false);
  const begin = useMutation({
    mutationFn: () => api.post<Saved>("/me/reaction-signals", { submission_id: submission }),
    onSuccess: (out) => {
      setEventId(out.adverse_event_id);
      void client.invalidateQueries({ queryKey: ["my-reaction-pending"] });
    },
  });
  const complete = useMutation({
    mutationFn: () => api.put<Saved>(`/me/reaction-signals/${eventId}`, { trade_name: name, description, onset_text: onset }),
    onSuccess: () => {
      setSaved(true);
      void client.invalidateQueries({ queryKey: ["my-reaction-pending"] });
    },
  });
  const yes = () => {
    setAnswer("yes");
    const existing = pending.data?.[0];
    if (existing) setEventId(existing.adverse_event_id);
    else begin.mutate();
  };
  if (saved) return <div className="reaction-question" role="status">
    <p>Сообщение сохранено.</p>
    <button className="link-btn" type="button" onClick={() => {
      setSubmission(newSubmissionId()); setEventId(null); setAnswer(null); setDescription(""); setOnset(""); setSaved(false);
    }}>Сообщить ещё</button>
  </div>;
  return <section className="reaction-question" style={{ marginTop: 20, borderTop: "1px solid #ddd", paddingTop: 16 }}>
    <p id={`${id}-question`}><strong>После приёма препарата вам стало хуже физически или эмоционально?</strong></p>
    {answer !== "yes" && <div className="two" aria-labelledby={`${id}-question`}>
      <button type="button" className="btn btn-s" disabled={begin.isPending || pending.isPending || pending.isError} onClick={yes}>Да</button>
      <button type="button" className={`btn ${answer === "no" ? "btn-p" : "btn-s"}`} onClick={() => setAnswer("no")}>Нет</button>
    </div>}
    {answer !== "yes" && pending.data && pending.data.length > 0 && <button type="button" className="link-btn" onClick={yes}>Дополнить начатое сообщение</button>}
    {pending.isError && <ErrorBox error={pending.error} onRetry={() => void pending.refetch()} />}
    {begin.isPending && <p>Сохраняем сообщение…</p>}
    {begin.isError && <ErrorBox error={begin.error} onRetry={() => begin.mutate()} />}
    {eventId && <form onSubmit={(e) => { e.preventDefault(); complete.mutate(); }} style={{ display: "grid", gap: 12 }}>
      <label className="fld">Торговое название препарата
        <input className="inp" required maxLength={300} list={`${id}-drugs`} value={name} onChange={(e) => setName(e.target.value)} />
        <span className="sub">Выберите своё назначение или напишите название с упаковки.</span>
      </label>
      <datalist id={`${id}-drugs`}>{[...new Set((prescription.data ?? []).map((line) => line.trade_name))].map((n) => <option key={n} value={n} />)}</datalist>
      <label className="fld">Что произошло?
        <textarea className="inp" required rows={4} maxLength={5000} value={description} onChange={(e) => setDescription(e.target.value)} placeholder="Опишите своими словами, что изменилось в вашем самочувствии" />
      </label>
      <label className="fld">Когда это началось?
        <input className="inp" required maxLength={500} value={onset} onChange={(e) => setOnset(e.target.value)} placeholder="Например: вчера вечером, через час после таблетки" />
      </label>
      {complete.isError && <ErrorBox error={complete.error} />}
      <button className="btn btn-p" type="submit" disabled={complete.isPending || !name.trim() || !description.trim() || !onset.trim()}>Сохранить сообщение</button>
    </form>}
  </section>;
}
