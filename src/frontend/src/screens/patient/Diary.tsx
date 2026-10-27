/**
 * «Дневник»: the patient's own entries (answers only, never triage) and the check-in flow
 * (boards P-Diary, P-DiaryText). After saving the patient sees only that the entry is saved.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { api, newSubmissionId } from "../../api/client";
import type { ComplaintCatalog, DiaryEntry } from "../../api/types";
import { dayMonthLong, longDay } from "../../lib/format";
import { useScreen } from "../../ui/screen";
import { ErrorBox, Q } from "../../ui/states";
import {
  EMPTY_DRAFT,
  MOODS,
  STEPS,
  buildDiaryPayload,
  firstStep,
  localDay,
  moodLabel,
  nextStep,
  parseMood,
  parseSleepDuration,
  sleepDurationLabel,
  prevStep,
  progress,
  sleepLabel,
  stepNumber,
  type DiaryDraft,
  type DiaryStep,
} from "./diaryFlow";
import { FlowScreen, SavedScreen } from "./Flow";
import { MoodFace } from "./MoodFace";
import { useComplaints, useMyDiary } from "./patientApi";
import "./patient.css";

export function PatientDiary() {
  const [params] = useSearchParams();
  if (params.get("new") !== null || params.get("edit")) return <EditDiary initialMood={parseMood(params.get("mood"))} day={params.get("edit") ?? localDay()} />;
  return <DiaryEntries />;
}

/* ---------- entries ---------- */

function sinceDays(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() - days);
  return localDay(d);
}

function DiaryEntries() {
  useScreen("p-entries");
  const [days, setDays] = useState(30);
  const today = localDay();
  const entries = useMyDiary(sinceDays(days), today);
  const complaints = useComplaints();
  const names = complaintNames(complaints.data);
  const hasToday = (entries.data ?? []).some((e) => e.day === today);

  return (
    <main className="wrap narrow">
      <h1>Дневник</h1>
      <section className="card due">
        <div>
          <h2>{hasToday ? "Запись за сегодня есть" : "Запись за сегодня"}</h2>
          <div className="sub">{dayMonthLong(new Date())}</div>
        </div>
        <Link className={`btn ${hasToday ? "btn-s" : "btn-p"}`} to="/p/diary?new=1" style={{ flex: "none" }}>
          {hasToday ? "Дополнить" : "Заполнить"}
        </Link>
      </section>
      <section className="card">
        <h2>Мои записи</h2>
        <Q query={entries}>
          {(list) =>
            list.length === 0 ? (
              <div className="sub" style={{ marginTop: 8 }}>
                Записей нет
              </div>
            ) : (
              <div className="hist">
                {[...list]
                  .sort((a, b) => (a.day === b.day ? b.saved_at.localeCompare(a.saved_at) : b.day.localeCompare(a.day)))
                  .map((e) => (
                    <EntryRow key={e.revision_id} entry={e} names={names} />
                  ))}
              </div>
            )
          }
        </Q>
        {entries.isSuccess && (
          <button type="button" className="btn btn-t more-btn" onClick={() => setDays((d) => d * 3)}>
            Показать раньше
          </button>
        )}
      </section>
    </main>
  );
}

function complaintNames(catalog: ComplaintCatalog | undefined): Map<string, string> {
  const out = new Map<string, string>();
  for (const c of catalog?.categories ?? []) for (const item of c.complaints) out.set(item.code, item.name);
  return out;
}

export function entrySummary(e: DiaryEntry): string[] {
  const parts: string[] = [];
  const mood = e.schema_version === 2 ? moodLabel(e.mood) : e.mood === null ? null : `${e.mood} из 10 (прежняя шкала)`;
  if (mood) parts.push(`настроение: ${mood.toLowerCase()}`);
  if (e.anxiety !== null) parts.push(`тревога ${e.anxiety} из ${e.schema_version === 2 ? 3 : 10}`);
  if (e.irritability != null) parts.push(`раздражительность ${e.irritability} из 3`);
  if (e.sleep_hours != null) parts.push(`сон: ${sleepDurationLabel(e.sleep_hours)}`);
  if (e.unusual_experiences != null) parts.push(`необычные проявления: ${e.unusual_experiences ? "да" : "нет"}`);
  if (e.energy !== null) parts.push(`энергия ${e.energy} из 10`);
  const sleep = sleepLabel(e.sleep ?? null);
  if (sleep) parts.push(`сон: ${sleep.toLowerCase()}`);
  return parts;
}

function EntryRow({ entry, names }: { entry: DiaryEntry; names: Map<string, string> }) {
  const summary = entrySummary(entry);
  return (
    <div className="hr">
      <div className="entry">
        <div style={{ fontWeight: 500 }}>{longDay(entry.day)}</div>
        {summary.length > 0 && <div className="sub">{summary.join(" · ")}</div>}
        {entry.complaints.length > 0 && (
          <div className="sub">{entry.complaints.map((c) => names.get(c) ?? c).join(", ")}</div>
        )}
        {entry.side_effects.length > 0 && <div className="sub">Побочные эффекты: {entry.side_effects.map((c) => names.get(c) ?? c).join(", ")}</div>}
        {entry.self_harm_thoughts !== null && <div className="sub">Мысли о самоповреждении: {entry.self_harm_thoughts ? "да" : "нет"}</div>}
        {entry.text && <p className="text" style={{ whiteSpace: "pre-wrap" }}>{entry.text}</p>}
        {canEdit(entry) && <Link to={`/p/diary?edit=${entry.day}`}>Редактировать</Link>}
      </div>
    </div>
  );
}

/* ---------- check-in flow ---------- */

export function canEdit(entry: DiaryEntry, now = Date.now()): boolean {
  return now - new Date(entry.created_at ?? entry.saved_at).getTime() <= 86_400_000;
}

function EditDiary({ initialMood, day }: { initialMood: number | null; day: string }) {
  const entries = useMyDiary(day, day);
  return <Q query={entries}>{(list) => {
    const entry = list[0];
    if (entry && !canEdit(entry)) return <main className="wrap narrow"><p>Записи старше суток нельзя редактировать.</p><Link to="/p/diary">К дневнику</Link></main>;
    const start: DiaryDraft = entry ? {
      mood: entry.schema_version === 2 ? entry.mood : null, anxiety: entry.schema_version === 2 ? entry.anxiety : null,
      irritability: entry.irritability ?? null, sleep_hours: entry.sleep_hours ?? null,
      unusual_experiences: entry.unusual_experiences ?? null,
      self_harm_thoughts: entry.schema_version === 2 ? entry.self_harm_thoughts : null, text: entry.text ?? "",
    } : { ...EMPTY_DRAFT, mood: initialMood };
    return <DiaryFlow key={day} start={start} day={day} editing={Boolean(entry)} />;
  }}</Q>;
}

function DiaryFlow({ start, day, editing }: { start: DiaryDraft; day: string; editing: boolean }) {
  const [draft, setDraft] = useState<DiaryDraft>(start);
  const [step, setStep] = useState<DiaryStep>(editing ? "mood" : firstStep(start));
  // One submission id per entry: a retry after a network error can't create a second entry.
  const [submissionId] = useState(newSubmissionId);
  const queryClient = useQueryClient();
  const navigate = useNavigate();

  const save = useMutation({
    mutationFn: () => api.post<DiaryEntry>("/me/diary", buildDiaryPayload(draft, day, submissionId)),
    onSuccess: () => queryClient.invalidateQueries({ queryKey: ["me", "diary"] }),
  });

  if (save.isSuccess) return <DiarySaved day={day} />;

  const set = (patch: Partial<DiaryDraft>) => setDraft((d) => ({ ...d, ...patch }));
  const back = prevStep(step);
  const next = nextStep(step);
  const n = stepNumber(step);

  return (
    <FlowScreen screen={step === "text" || step === "unusual" || step === "self_harm" ? "p-diarytext" : "p-diary"} closeLabel="Закрыть дневник">
      <div className="stepline">
        <span>Дневник · {dayMonthLong(day)}</span>
        <span>
          {n} из {STEPS.length}
        </span>
      </div>
      <div className="prog" role="progressbar" aria-valuemin={1} aria-valuemax={STEPS.length} aria-valuenow={n} aria-label={`Шаг ${n} из ${STEPS.length}`}>
        <i style={{ width: progress(step) }} />
      </div>

      <StepBody step={step} draft={draft} set={set} />

      {save.isError && <ErrorBox error={save.error} />}
      <div className="foot">
        {back ? (
          <button type="button" className="btn btn-s" onClick={() => setStep(back)}>
            Назад
          </button>
        ) : (
          <button type="button" className="btn btn-s" onClick={() => void navigate(-1)}>
            Отмена
          </button>
        )}
        {next ? (
          <button type="button" className="btn btn-p" disabled={step === "sleep" && draft.sleep_hours !== null && (!Number.isFinite(draft.sleep_hours) || draft.sleep_hours < 0 || draft.sleep_hours > 24)} onClick={() => setStep(next)}>
            Дальше
          </button>
        ) : (
          <button type="button" className="btn btn-p" disabled={save.isPending} onClick={() => save.mutate()}>
            {save.isPending ? "Сохраняем…" : save.isError ? "Повторить" : "Сохранить запись"}
          </button>
        )}
      </div>
    </FlowScreen>
  );
}

function StepBody({ step, draft, set }: { step: DiaryStep; draft: DiaryDraft; set: (patch: Partial<DiaryDraft>) => void }) {
  switch (step) {
    case "mood":
      return (
        <section className="card">
          <h1>Как вы сегодня?</h1>
          <div className="moods" role="radiogroup" aria-label="Настроение">
            {MOODS.map((m) => (
              <button
                key={m.value}
                type="button"
                role="radio"
                aria-checked={draft.mood === m.value}
                className={`mood${draft.mood === m.value ? " on" : ""}`}
                onClick={() => set({ mood: draft.mood === m.value ? null : m.value })}
              >
                <MoodFace mouth={m.mouth} />
                {m.label}
              </button>
            ))}
          </div>
        </section>
      );
    case "anxiety":
      return (
        <ScoreStep title="Насколько вам тревожно сегодня?" label="Тревога от 0 до 3" low="совсем нет" high="очень сильно" value={draft.anxiety} onChange={(anxiety) => set({ anxiety })} />
      );
    case "irritability":
      return (
        <ScoreStep title="Насколько вы раздражительны сегодня?" label="Раздражительность от 0 до 3" low="совсем нет" high="очень сильно" value={draft.irritability} onChange={(irritability) => set({ irritability })} />
      );
    case "sleep":
      return <SleepStep value={draft.sleep_hours} onChange={(sleep_hours) => set({ sleep_hours })} />;
    case "unusual":
      return (
        <section className="card">
          <h1>Были ли необычные мысли, галлюцинации или другие проявления?</h1>
          <div className="yn" role="radiogroup" aria-label="Необычные проявления">
            {[
              { value: true, label: "Да" },
              { value: false, label: "Нет" },
            ].map((o) => (
              <button
                key={o.label}
                type="button"
                role="radio"
                aria-checked={draft.unusual_experiences === o.value}
                className={`opt${draft.unusual_experiences === o.value ? " on" : ""}`}
                onClick={() => set({ unusual_experiences: draft.unusual_experiences === o.value ? null : o.value })}
              >
                {o.label}
              </button>
            ))}
          </div>
          {draft.unusual_experiences === true && <p role="status">Укажите подробнее в следующем окне</p>}
        </section>
      );
    case "self_harm":
      return (
        <section className="card">
          <h1>Были ли мысли причинить себе вред?</h1>
          <div className="yn" role="radiogroup" aria-label="Мысли причинить себе вред">
            {[
              { value: true, label: "Да" },
              { value: false, label: "Нет" },
            ].map((o) => (
              <button
                key={o.label}
                type="button"
                role="radio"
                aria-checked={draft.self_harm_thoughts === o.value}
                className={`opt${draft.self_harm_thoughts === o.value ? " on" : ""}`}
                onClick={() => set({ self_harm_thoughts: draft.self_harm_thoughts === o.value ? null : o.value })}
              >
                {o.label}
              </button>
            ))}
          </div>
        </section>
      );
    case "text":
      return (
        <section className="card">
          <h1>Ежедневные заметки</h1>
          <label htmlFor="diary-text" className="sub" style={{ display: "block", margin: "6px 0 12px" }}>
            Что сегодня было важным
          </label>
          <textarea id="diary-text" className="inp" value={draft.text} maxLength={4000} onChange={(e) => set({ text: e.target.value })} />
        </section>
      );
  }
}

function SleepStep({ value, onChange }: { value: number | null; onChange: (value: number | null) => void }) {
  const initial = value !== null && Number.isFinite(value) ? sleepDurationLabel(value).split(":") : ["", ""];
  const [hours, setHours] = useState(initial[0] ?? "");
  const [minutes, setMinutes] = useState(initial[1] ?? "");
  const invalid = Number.isNaN(parseSleepDuration(hours, minutes));
  return (
    <section className="card">
      <h1>Сколько вы спали прошедшей ночью?</h1>
      <fieldset className="sleep-duration" onBlur={(event) => {
        if (event.currentTarget.contains(event.relatedTarget as Node | null)) return;
        const duration = parseSleepDuration(hours, minutes);
        if (duration !== null && Number.isFinite(duration)) {
          const [h, m] = sleepDurationLabel(duration).split(":");
          setHours(h ?? "");
          setMinutes(m ?? "00");
          onChange(duration);
        }
      }}>
        <legend>Длительность сна</legend>
        <div className="sleep-duration-fields">
          <label>Часы<input className="inp" aria-label="Часы сна" inputMode="numeric" type="text" maxLength={2}
            placeholder="чч" value={hours} aria-invalid={invalid} aria-describedby="sleep-hint"
            onChange={(event) => { setHours(event.target.value); onChange(parseSleepDuration(event.target.value, minutes)); }} /></label>
          <span aria-hidden="true">:</span>
          <label>Минуты<input className="inp" aria-label="Минуты сна" inputMode="numeric" type="text" maxLength={2}
            placeholder="мм" value={minutes} aria-invalid={invalid} aria-describedby="sleep-hint"
            onChange={(event) => { setMinutes(event.target.value); onChange(parseSleepDuration(hours, event.target.value)); }} /></label>
        </div>
        <p id="sleep-hint" className={invalid ? "err" : "sub"}>
          {invalid ? "Укажите часы от 00 до 24 и минуты от 00 до 59. Максимум — 24:00." : "Часы и минуты, например 07:30. Если минуты не указаны, сохраним 00."}
        </p>
      </fieldset>
    </section>
  );
}

function ScoreStep(props: { title: string; label: string; low: string; high: string; value: number | null; onChange: (v: number | null) => void }) {
  return (
    <section className="card">
      <h1>{props.title}</h1>
      <div className="diary-score-options" role="radiogroup" aria-label={props.label}>
        {Array.from({ length: 4 }, (_, v) => (
          <button
            key={v}
            type="button"
            role="radio"
            aria-checked={props.value === v}
            className={props.value === v ? "on" : ""}
            onClick={() => props.onChange(props.value === v ? null : v)}
          >
            <span className="diary-score-number">{v}</span>
            <span>{["Нет", "Слабая", "Умеренная", "Выраженная"][v]}</span>
          </button>
        ))}
      </div>
      <div className="ends">
        <span>{props.low}</span>
        <span>{props.high}</span>
      </div>
    </section>
  );
}

function DiarySaved({ day }: { day: string }) {
  return <SavedScreen caption={`Дневник · ${dayMonthLong(day)}`} title="Запись сохранена" />;
}
