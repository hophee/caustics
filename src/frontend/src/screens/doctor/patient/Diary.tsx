/** «Дневник» tab (board I2-Diary): one chart per metric, the entries with triage and doctor notes. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { DiaryCharts } from "../../patient/DiaryCharts";
import { useMemo, useState } from "react";
import { api } from "../../../api/client";
import type { DoctorDiaryEntry } from "../../../api/types";
import { date, dayMonth, time } from "../../../lib/format";
import { useScreen } from "../../../ui/screen";
import { ErrorBox, Loading, errorText } from "../../../ui/states";
import { EntryTriage } from "./parts/EntryTriage";
import { useCasePeriod, useComplaints, useDiary, usePatientId } from "./parts/data";
import { FILTERS, type Filter, matches, metricsLine, triageSource } from "./parts/diary";
import { type Period, daysBetween, localDay } from "./parts/period";
import { plural } from "../../../lib/format";
import "./Diary.css";

export function DoctorDiary() {
  useScreen("diary");
  const patientId = usePatientId();
  const period = useCasePeriod(patientId);
  if (!period) return <Loading />;
  return <DiaryBody patientId={patientId} period={period} />;
}

function ago(day: string): string {
  const n = daysBetween(day, localDay());
  if (n <= 0) return "сегодня";
  if (n === 1) return "вчера";
  return `${n} ${plural(n, "день", "дня", "дней")} назад`;
}

function DiaryBody({ patientId, period }: { patientId: string; period: Period }) {
  const diary = useDiary(patientId, period.start);
  const complaints = useComplaints().data;
  const [filter, setFilter] = useState<Filter>("all");
  const names = useMemo(() => {
    const map = new Map<string, string>();
    for (const cat of complaints?.categories ?? []) for (const c of cat.complaints) map.set(c.code, c.name);
    return (code: string) => map.get(code) ?? code;
  }, [complaints]);

  if (diary.isPending) return <Loading />;
  if (diary.isError) return <ErrorBox error={diary.error} onRetry={() => void diary.refetch()} />;
  const entries = diary.data;
  const newest = [...entries].sort((a, b) => b.day.localeCompare(a.day) || b.saved_at.localeCompare(a.saved_at));
  const shown = newest.filter((e) => matches(e, filter));

  return (
    <>
      <DiaryCharts entries={entries} period={period} />
      <section className="card">
        <div className="ch">
          <h2>Записи</h2>
          <div className="filters" role="group" aria-label="Фильтр записей">
            {FILTERS.map((f) => (
              <button
                key={f.key}
                type="button"
                className={`f${filter === f.key ? " on" : ""}`}
                aria-pressed={filter === f.key}
                onClick={() => setFilter(f.key)}
              >
                {f.label} · {entries.filter((e) => matches(e, f.key)).length}
              </button>
            ))}
          </div>
        </div>
        {shown.length === 0 && <div className="empty">Нет записей</div>}
        {shown.map((e) => (
          <EntryRow key={e.entry_id} entry={e} patientId={patientId} names={names} />
        ))}
      </section>
    </>
  );
}

function EntryRow({ entry, patientId, names }: { entry: DoctorDiaryEntry; patientId: string; names: (code: string) => string }) {
  const [writing, setWriting] = useState(false);
  const source = triageSource(entry.triage);
  return (
    <div className="entry">
      <div>
        <div style={{ fontWeight: 500 }}>
          {dayMonth(entry.day)}, {time(entry.saved_at)}
        </div>
        <div className="sub">{ago(entry.day)}</div>
      </div>
      <div>
        {entry.text ? <div>«{entry.text}»</div> : <div className="muted">без текста</div>}
        <div className="sub" style={{ marginTop: 4 }}>
          {metricsLine(entry, names, true)}
        </div>
        {entry.notes.map((n) => (
          <div key={n.note_id} className="note">
            <b style={{ fontWeight: 500 }}>Заметка {date(n.created_at)}:</b> {n.text}
          </div>
        ))}
        {writing && <NoteForm entryId={entry.entry_id} patientId={patientId} onDone={() => setWriting(false)} />}
      </div>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6 }}>
        <EntryTriage triage={entry.triage} />
        {entry.triage?.model_unsure && <span className="lv lv-gry">модель не уверена</span>}
        {entry.triage?.model_pending && <span className="lv lv-gry">текст анализируется</span>}
        {entry.triage?.model_unavailable && <span className="lv lv-gry">модель недоступна</span>}
        {source && <span className="sub">{source}</span>}
        {!writing && (
          <button type="button" className="linkbtn sub" onClick={() => setWriting(true)}>
            Заметка
          </button>
        )}
      </div>
    </div>
  );
}

function NoteForm({ entryId, patientId, onDone }: { entryId: string; patientId: string; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [text, setText] = useState("");
  const save = useMutation({
    mutationFn: () => api.post(`/diary/${entryId}/notes`, { text: text.trim() }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["patient", patientId, "diary"] });
      onDone();
    },
  });
  return (
    <form
      className="note"
      style={{ display: "flex", flexDirection: "column", gap: 8 }}
      onSubmit={(event) => {
        event.preventDefault();
        if (text.trim()) save.mutate();
      }}
    >
      <label className="sr" htmlFor={`note-${entryId}`}>
        Заметка
      </label>
      <textarea id={`note-${entryId}`} className="inp" value={text} onChange={(e) => setText(e.target.value)} autoFocus />
      {save.isError && <span className="form-err">{errorText(save.error)}</span>}
      <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
        <button type="button" className="btn btn-t" onClick={onDone}>
          Отменить
        </button>
        <button type="submit" className="btn btn-p" disabled={!text.trim() || save.isPending}>
          Сохранить
        </button>
      </div>
    </form>
  );
}
