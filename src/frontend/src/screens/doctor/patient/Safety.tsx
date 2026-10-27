/** «Профиль безопасности» tab (board I2-Profile): explicit answers, saved as one full profile. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api } from "../../../api/client";
import { useActiveCase, useDiagnoses, usePatientSummary, useProfile } from "../../../api/queries";
import type { Profile, Tristate } from "../../../api/types";
import { useScreen } from "../../../ui/screen";
import { ErrorBox, Loading } from "../../../ui/states";
import { usePatientId } from "./parts/data";
import {
  type ListKey,
  type ListState,
  PICKED_FROM,
  type SafetyForm,
  type Suggestion,
  type Vocabulary,
  addItem,
  formOf,
  inVocabulary,
  profileBody,
  suggest,
  validate,
} from "./parts/safety";
import { comorbid, mainCodes, showsPregnancy } from "./parts/view";

import { CurrentMedicationEditor, persistMedicationDraft, useMedicationDraft } from "./visit/CurrentMedicationEditor";
import { DiagnosisSearch } from "./visit/SearchSelect";
import "./VisitWorkspace.css";

export function Safety() {
  useScreen("profile");
  const patientId = usePatientId();
  const profile = useProfile(patientId);
  const summary = usePatientSummary(patientId).data;
  const activeCase = useActiveCase(patientId).data;
  const diagnoses = useDiagnoses(patientId, activeCase?.case_id);
  if (profile.isPending || (activeCase && diagnoses.isPending)) return <Loading />;
  if (profile.isError) return <ErrorBox error={profile.error} onRetry={() => void profile.refetch()} />;
  const main = mainCodes(diagnoses.data, summary?.main_icd ?? null);
  return <SafetyEditor key={JSON.stringify({...profile.data, current_medications_reviewed_at: null})} patientId={patientId} profile={profile.data} main={main} />;
}

function Seg<T extends string>({ value, options, onChange, label }: { value: T; options: [T, string][]; onChange: (v: T) => void; label: string }) {
  return (
    <span className="seg" role="radiogroup" aria-label={label}>
      {options.map(([key, text]) => (
        <button
          key={key}
          type="button"
          role="radio"
          aria-checked={value === key}
          className={[value === key ? "on" : "", key === "unknown" ? "nd" : ""].filter(Boolean).join(" ")}
          onClick={() => onChange(key)}
        >
          {text}
        </button>
      ))}
    </span>
  );
}

const TRI: [Tristate, string][] = [
  ["yes", "Да"],
  ["no", "Нет"],
  ["unknown", "Неизвестно"],
];
const LIST: [ListState, string][] = [
  ["none", "Нет"],
  ["some", "Есть"],
  ["unknown", "Неизвестно"],
];
const RISK: [Profile["suicide_risk"], string][] = [
  ["low", "Низкий"],
  ["high", "Высокий"],
  ["unknown", "Не оценён"],
];

function AddInline({ label, onAdd, normalize }: { label: string; onAdd: (v: string) => void; normalize?: (v: string) => string | null }) {
  const [value, setValue] = useState("");
  const [bad, setBad] = useState(false);
  const submit = () => {
    const v = normalize ? normalize(value) : value.trim();
    if (!v) {
      setBad(Boolean(value.trim()));
      return;
    }
    onAdd(v);
    setValue("");
    setBad(false);
  };
  return (
    <div className="inline-form">
      <input
        className="inp"
        aria-label={label}
        placeholder={label}
        value={value}
        aria-invalid={bad}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={(e) => {
          if (e.key === "Enter") {
            e.preventDefault();
            submit();
          }
        }}
      />
      <button type="button" className="btn btn-t" onClick={submit}>
        Добавить
      </button>
      {bad && <span className="form-err">Неверный код МКБ-10</span>}
    </div>
  );
}

/** Input with a dropdown of names from the ОХЛП base; only a picked row is added. */
function PickInline({ label, kind, vocabulary, onAdd }: { label: string; kind: "drugs" | "excipients"; vocabulary: Vocabulary; onAdd: (values: string[]) => void }) {
  const [value, setValue] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const listId = useId();
  const options = open ? suggest(vocabulary, kind, value) : [];
  const shown = open && (options.length > 0 || value.trim() !== "");
  const pick = (s: Suggestion | undefined) => {
    if (!s) return;
    onAdd(s.values);
    setValue("");
    setActive(0);
  };
  return (
    <div className="pick">
      <input
        className="inp"
        role="combobox"
        aria-label={label}
        aria-expanded={shown}
        aria-controls={listId}
        aria-autocomplete="list"
        aria-activedescendant={options[active] ? `${listId}-${active}` : undefined}
        placeholder={kind === "drugs" ? "МНН или торговое название" : label}
        value={value}
        onFocus={() => setOpen(true)}
        onBlur={() => setOpen(false)}
        onChange={(e) => {
          setValue(e.target.value);
          setOpen(true);
          setActive(0);
        }}
        onKeyDown={(e) => {
          if (e.key === "ArrowDown" || e.key === "ArrowUp") {
            e.preventDefault();
            const step = e.key === "ArrowDown" ? 1 : -1;
            setActive((a) => (options.length ? (a + step + options.length) % options.length : 0));
          } else if (e.key === "Enter") {
            e.preventDefault();
            pick(options[active]);
          } else if (e.key === "Escape") {
            setOpen(false);
          }
        }}
      />
      {shown && (
        <ul className="pick-list" role="listbox" id={listId} aria-label={label}>
          {options.length === 0 && <li className="muted">Нет в базе ОХЛП</li>}
          {options.map((s, i) => (
            <li
              key={s.key}
              id={`${listId}-${i}`}
              role="option"
              aria-selected={i === active}
              onMouseDown={(e) => e.preventDefault()}
              onMouseEnter={() => setActive(i)}
              onClick={() => pick(s)}
            >
              <span>{s.label}</span>
              {s.mnn && <span className="sub">{s.mnn}</span>}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}

function useVocabulary() {
  return useQuery({
    queryKey: ["kb", "vocabulary"],
    queryFn: () => api.get<Vocabulary>("/drugs/vocabulary"),
    staleTime: Infinity,
  });
}

function ListFact({
  label,
  field,
  error,
  onChange,
  pick,
  vocabulary,
}: {
  label: string;
  field: SafetyForm["lists"][ListKey];
  error?: string;
  onChange: (f: SafetyForm["lists"][ListKey]) => void;
  pick?: "drugs" | "excipients";
  vocabulary?: Vocabulary;
}) {
  return (
    <div className="lst">
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12, flexWrap: "wrap" }}>
        <span>{label}</span>
        <Seg label={label} value={field.state} options={LIST} onChange={(state) => onChange({ ...field, state })} />
      </div>
      {field.state === "some" && (
        <>
          {field.items.map((item) => (
            <div key={item} className="item">
              <span>
                {item}
                {pick && vocabulary && !inVocabulary(vocabulary, pick, item) && (
                  <span className="chip nd" style={{ marginLeft: 8 }}>
                    нет в базе
                  </span>
                )}
              </span>
              <button type="button" className="linkbtn sub" onClick={() => onChange({ ...field, items: field.items.filter((i) => i !== item) })}>
                Удалить
              </button>
            </div>
          ))}
          {pick && vocabulary ? (
            <PickInline label={label} kind={pick} vocabulary={vocabulary} onAdd={(vs) => onChange({ ...field, items: vs.reduce(addItem, field.items) })} />
          ) : (
            <AddInline label={label} onAdd={(v) => onChange({ ...field, items: addItem(field.items, v) })} />
          )}
        </>
      )}
      {error && <span className="form-err">{error}</span>}
    </div>
  );
}

function SafetyEditor({ patientId, profile, main }: { patientId: string; profile: Profile; main: string[] }) {
  const queryClient = useQueryClient();
  const initial = formOf(profile, comorbid(profile.diagnoses, main));
  const [form, setForm] = useState<SafetyForm>(initial);
  const [tried, setTried] = useState(false);
  const errors = validate(form);
  const [medicationDraft, setMedicationDraft] = useMedicationDraft(patientId);
  const dirty = JSON.stringify(form) !== JSON.stringify(initial) || medicationDraft !== null;
  const vocabulary = useVocabulary().data;

  const save = useMutation({
    mutationFn: async () => {
      if (medicationDraft) {
        await persistMedicationDraft(patientId, medicationDraft);
        setMedicationDraft(null);
        await queryClient.invalidateQueries({queryKey: ["patient", patientId, "therapy"]});
      }
      return api.put<Profile>(`/patients/${patientId}/profile`, profileBody(profile, form, main));
    },
    onSuccess: (data) => {
      queryClient.setQueryData(["patient", patientId, "profile"], data);
      void queryClient.invalidateQueries({ queryKey: ["patient", patientId] });
    },
  });
  const set = <K extends keyof SafetyForm>(key: K, value: SafetyForm[K]) => setForm((f) => ({ ...f, [key]: value }));
  const setList = (key: ListKey, value: SafetyForm["lists"][ListKey]) => setForm((f) => ({ ...f, lists: { ...f.lists, [key]: value } }));
  const shown = tried ? errors : null;

  return (
    <form
      className="form"
      onSubmit={(event) => {
        event.preventDefault();
        setTried(true);
        if (!errors && dirty) save.mutate();
      }}
    >
      {showsPregnancy(profile.sex) && (
        <section className="card">
          <div className="ch">
            <h2>Беременность и лактация</h2>
          </div>
          <div className="q">
            <span>Беременность</span>
            <Seg label="Беременность" value={form.pregnant} options={TRI} onChange={(v) => set("pregnant", v)} />
          </div>
          <div className="q">
            <span>Лактация</span>
            <Seg label="Лактация" value={form.lactating} options={TRI} onChange={(v) => set("lactating", v)} />
          </div>
        </section>
      )}
      <section className="card">
        <div className="ch">
          <h2>Аллергии и непереносимость</h2>
        </div>
        <ListFact label="Аллергии" field={form.lists.allergies} error={shown?.lists.allergies} onChange={(f) => setList("allergies", f)} />
        <ListFact
          label="Непереносимость препаратов"
          field={form.lists.past_intolerance}
          error={shown?.lists.past_intolerance}
          onChange={(f) => setList("past_intolerance", f)}
          pick={PICKED_FROM.past_intolerance}
          vocabulary={vocabulary}
        />
        <ListFact
          label="Вспомогательные вещества"
          field={form.lists.intolerant_excipients}
          error={shown?.lists.intolerant_excipients}
          onChange={(f) => setList("intolerant_excipients", f)}
          pick={PICKED_FROM.intolerant_excipients}
          vocabulary={vocabulary}
        />
        <ListFact
          label="Неэффективность в прошлом"
          field={form.lists.past_inefficacy}
          error={shown?.lists.past_inefficacy}
          onChange={(f) => setList("past_inefficacy", f)}
          pick={PICKED_FROM.past_inefficacy}
          vocabulary={vocabulary}
        />
      </section>
      <CurrentMedicationEditor patientId={patientId} />
      <section className="card">
        <div className="ch">
          <h2>Сопутствующие диагнозы</h2>
        </div>
        <div className="lst">
          {form.comorbid.length === 0 && <span>нет</span>}
          {form.comorbid.map((code) => (
            <div key={code} className="item">
              <span>{code}</span>
              <button type="button" className="linkbtn sub" onClick={() => set("comorbid", form.comorbid.filter((c) => c !== code))}>
                Удалить
              </button>
            </div>
          ))}
          <DiagnosisSearch value="" scope="contra"
            onChange={(code) => {if (!main.includes(code)) set("comorbid", addItem(form.comorbid, code));}} />
        </div>
      </section>
      <section className="card">
        <div className="ch">
          <h2>ПАВ, риск, измерения</h2>
        </div>
        <div className="q">
          <span>Употребление ПАВ в анамнезе / Употребление ПАВ активное</span>
          <Seg label="Употребление ПАВ в анамнезе / Употребление ПАВ активное" value={form.substance_use} options={TRI} onChange={(v) => set("substance_use", v)} />
        </div>
        <div className="q">
          <span>Суицидальный риск</span>
          <Seg label="Суицидальный риск" value={form.suicide_risk} options={RISK} onChange={(v) => set("suicide_risk", v)} />
        </div>
        <div className="two">
          <div className="fld">
            <label htmlFor="weight">Вес, кг</label>
            <input id="weight" className="inp" inputMode="decimal" value={form.weight} onChange={(e) => set("weight", e.target.value)} aria-invalid={Boolean(shown?.weight)} />
            {shown?.weight && <span className="form-err">{shown.weight}</span>}
          </div>
        </div>
      </section>
      {save.isError && <ErrorBox error={save.error} />}
      <div className="save">
        <button type="button" className="btn btn-t" disabled={!dirty || save.isPending} onClick={() => {setForm(initial); setMedicationDraft(null);}}>
          Отменить
        </button>
        <button type="submit" className="btn btn-p" disabled={!dirty || save.isPending}>
          {save.isSuccess && !dirty ? "Сохранено" : "Сохранить профиль"}
        </button>
      </div>
    </form>
  );
}
