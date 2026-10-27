/**
 * «Лекарства» (board P-Meds): today's doses with marks, the marks calendar, the confirmed prescription
 * (no warnings, ever), the reminder rule within the configured limits, and «I take another drug».
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api, ApiError, newSubmissionId } from "../../api/client";
import type { AdherenceOptions, ReminderRule } from "../../api/types";
import { longDay } from "../../lib/format";
import { useScreen } from "../../ui/screen";
import { ErrorBox, Loading, Q } from "../../ui/states";
import { localDay } from "./diaryFlow";
import { DoseMarker } from "./DoseMarker";
import { ReactionQuestion } from "./ReactionQuestion";
import { calendarDays, calendarRange, intakeTimes, lineSchedule, lineTitle, mg, reminderText } from "./doses";
import { meKeys, useAdherenceOptions, useMyDoses, usePrescription, useReminders } from "./patientApi";
import "./patient.css";

const WEEKDAYS = ["пн", "вт", "ср", "чт", "пт", "сб", "вс"];

export function Meds() {
  useScreen("p-meds");
  const now = new Date();
  const today = localDay(now);
  const doses = useMyDoses(today, today);
  const prescription = usePrescription();

  return (
    <main className="wrap">
      <div>
        <div className="muted" style={{ fontSize: 15 }}>
          {longDay(now)}
        </div>
        <h1>Лекарства</h1>
      </div>
      <div className="grid">
        <div className="col">
          {doses.isPending && <Loading />}
          {doses.isError && <ErrorBox error={doses.error} onRetry={() => void doses.refetch()} />}
          {(doses.data ?? []).map((dose) => (
            <section key={`${dose.course_id}|${dose.scheduled_at}`} className="card">
              <div className="dose">
                <div>
                  <h2>
                    {dose.name}, {mg(dose.amount_mg)}
                  </h2>
                </div>
                <div style={{ fontWeight: 600, fontSize: 18 }}>{dose.at}</div>
              </div>
              <DoseMarker dose={dose} />
            </section>
          ))}
          {doses.data?.length === 0 && prescription.data && prescription.data.length > 0 && (
            <section className="card">
              <h2>Сегодня приёмов нет</h2>
            </section>
          )}
        </div>
        <div className="col">
          <MarksCalendar now={now} />
          <section className="card rx">
            <h2>Моё назначение</h2>
            <Q query={prescription}>
              {(lines) =>
                lines.length === 0 ? (
                  <div className="sub">Назначений нет</div>
                ) : (
                  lines.map((line, i) => (
                    <div key={`${line.trade_name}|${i}`} className="rx-line">
                      <div style={{ fontWeight: 500 }}>{lineTitle(line)}</div>
                      <div className="sub">{lineSchedule(line)}</div>
                    </div>
                  ))
                )
              }
            </Q>
            <OtherDrug />
            <ReactionQuestion />
          </section>
          {prescription.data && prescription.data.length > 0 && <Reminders times={intakeTimes(prescription.data)} />}
        </div>
      </div>
    </main>
  );
}

function MarksCalendar({ now }: { now: Date }) {
  const range = calendarRange(now);
  const doses = useMyDoses(range.since, range.until);
  const days = calendarDays(doses.data ?? [], now);
  const counts = { tk: 0, sk: 0, nm: 0 };
  for (const d of days) if (d.mark === "tk" || d.mark === "sk" || d.mark === "nm") counts[d.mark] += 1;
  return (
    <section className="card">
      <h2>Отметки</h2>
      {doses.isError ? (
        <ErrorBox error={doses.error} onRetry={() => void doses.refetch()} />
      ) : (
        <div className="cal" role="img" aria-label={`Принято дней: ${counts.tk}, пропущено: ${counts.sk}, без отметки: ${counts.nm}`}>
          {WEEKDAYS.map((w) => (
            <span key={w} className="wd">
              {w}
            </span>
          ))}
          {days.map((d) => (
            <span key={d.day} className={`d ${d.mark}${d.today ? " today" : ""}`}>
              {d.date}
            </span>
          ))}
        </div>
      )}
      <div className="leg">
        <span>
          <i className="tk" />
          принял
        </span>
        <span>
          <i className="sk" />
          пропустил
        </span>
        <span>
          <i className="nm" />
          нет отметки
        </span>
      </div>
    </section>
  );
}

function OtherDrug() {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState("");
  const [saved, setSaved] = useState<string[]>([]);
  const [submissionId, setSubmissionId] = useState(newSubmissionId);
  const report = useMutation({
    mutationFn: (substance: string) => api.post("/me/drugs", { submission_id: submissionId, substance }),
    onSuccess: (_out, substance) => {
      setSaved((s) => [...s, substance]);
      setName("");
      setSubmissionId(newSubmissionId());
      setOpen(false);
    },
  });

  return (
    <>
      {saved.map((s) => (
        <div key={s} className="sub">
          Добавлено: {s}
        </div>
      ))}
      {!open ? (
        <button type="button" className="link-btn" style={{ marginTop: 10, fontSize: 15 }} onClick={() => setOpen(true)}>
          Я принимаю другой препарат
        </button>
      ) : (
        <form
          className="other-drug"
          onSubmit={(e) => {
            e.preventDefault();
            const substance = name.trim();
            if (substance) report.mutate(substance);
          }}
        >
          <label htmlFor="other-drug" className="sub">
            Название препарата
          </label>
          <input id="other-drug" className="inp" value={name} maxLength={200} autoFocus onChange={(e) => setName(e.target.value)} />
          {report.isError && <ErrorBox error={report.error} />}
          <div className="two">
            <button type="button" className="btn btn-s" onClick={() => setOpen(false)}>
              Отмена
            </button>
            <button type="submit" className="btn btn-p" disabled={!name.trim() || report.isPending}>
              Сохранить
            </button>
          </div>
        </form>
      )}
    </>
  );
}

function Reminders({ times }: { times: string[] }) {
  const rule = useReminders();
  const options = useAdherenceOptions();
  const [editing, setEditing] = useState(false);
  return (
    <section className="card rx">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
        <h2>Напоминание</h2>
        {!editing && rule.data && options.data && (
          <button type="button" className="link-btn" style={{ fontSize: 14 }} onClick={() => setEditing(true)}>
            Изменить
          </button>
        )}
      </div>
      <Q query={rule}>
        {(r) =>
          editing && options.data ? (
            <ReminderForm rule={r} options={options.data} onDone={() => setEditing(false)} />
          ) : (
            <div className="sub" style={{ marginTop: 4 }}>
              {reminderText(r, times)}
            </div>
          )
        }
      </Q>
    </section>
  );
}

const OFFSETS = [-60, -30, -15, 0, 15, 30];

function offsetLabel(m: number): string {
  if (m === 0) return "во время приёма";
  return m < 0 ? `за ${-m} мин до приёма` : `через ${m} мин после`;
}

function ReminderForm({ rule, options, onDone }: { rule: ReminderRule; options: AdherenceOptions; onDone: () => void }) {
  const limits = options.reminder_limits;
  const [draft, setDraft] = useState<ReminderRule>(rule);
  const queryClient = useQueryClient();
  const save = useMutation({
    mutationFn: (body: ReminderRule) => api.put<ReminderRule>("/me/reminders", body),
    onSuccess: (out) => {
      queryClient.setQueryData(meKeys.reminders, out);
      onDone();
    },
  });
  const set = (patch: Partial<ReminderRule>) => setDraft((d) => ({ ...d, ...patch }));
  const offsets = OFFSETS.includes(draft.first_offset_minutes) ? OFFSETS : [...OFFSETS, draft.first_offset_minutes].sort((a, b) => a - b);
  const fieldError = (pointer: string) => (save.error instanceof ApiError ? save.error.detail(pointer)?.message : undefined);

  return (
    <form
      className="rem-form"
      onSubmit={(e) => {
        e.preventDefault();
        save.mutate(draft);
      }}
    >
      <div className="yn" role="radiogroup" aria-label="Напоминания">
        {[
          { value: true, label: "Включены" },
          { value: false, label: "Выключены" },
        ].map((o) => (
          <button key={o.label} type="button" role="radio" aria-checked={draft.enabled === o.value} className={`opt${draft.enabled === o.value ? " on" : ""}`} onClick={() => set({ enabled: o.value })}>
            {o.label}
          </button>
        ))}
      </div>
      {draft.enabled && (
        <>
          <label className="fld">
            <span className="sub">Первое напоминание</span>
            <select className="inp" value={draft.first_offset_minutes} onChange={(e) => set({ first_offset_minutes: Number(e.target.value) })}>
              {offsets.map((m) => (
                <option key={m} value={m}>
                  {offsetLabel(m)}
                </option>
              ))}
            </select>
          </label>
          <label className="fld">
            <span className="sub">Повторов, если нет отметки</span>
            <select className="inp" value={draft.max_repeats} onChange={(e) => set({ max_repeats: Number(e.target.value) })}>
              {Array.from({ length: limits.max_repeats + 1 }, (_, n) => (
                <option key={n} value={n}>
                  {n === 0 ? "без повторов" : n}
                </option>
              ))}
            </select>
            {fieldError("max_repeats") && <span className="err">{fieldError("max_repeats")}</span>}
          </label>
          {draft.max_repeats > 0 && (
            <label className="fld">
              <span className="sub">Повтор через, мин</span>
              <input
                className="inp"
                type="number"
                min={limits.min_repeat_every_minutes}
                step={5}
                value={draft.repeat_every_minutes}
                onChange={(e) => set({ repeat_every_minutes: Number(e.target.value) })}
              />
              {fieldError("repeat_every_minutes") && <span className="err">{fieldError("repeat_every_minutes")}</span>}
            </label>
          )}
        </>
      )}
      {save.isError && <ErrorBox error={save.error} />}
      <div className="two">
        <button type="button" className="btn btn-s" onClick={onDone}>
          Отмена
        </button>
        <button type="submit" className="btn btn-p" disabled={save.isPending}>
          Сохранить
        </button>
      </div>
    </form>
  );
}
