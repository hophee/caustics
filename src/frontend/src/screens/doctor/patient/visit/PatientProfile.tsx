import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../../../api/client";
import type { Profile } from "../../../../api/types";
import { DiagnosisSearch, SearchSelect } from "./SearchSelect";
import { ErrorBox } from "../../../../ui/states";
interface Vocabulary { substances: string[]; trade_names: {name: string; substances: string[]}[]; excipients: string[]; aliases?: Record<string, string> }
const norm = (s: string) => s.toLocaleLowerCase("ru").replace(/ё/g, "е").trim();
export function PatientProfile({ value, main, onChange }: { value: Profile; main: string; onChange: (p: Profile) => void }) {
  const [q, setQ] = useState("");
  const [fact, setFact] = useState<"allergies" | "past_intolerance">("past_intolerance");
  const vocab = useQuery({queryKey: ["kb", "vocabulary"], queryFn: () => api.get<Vocabulary>("/drugs/vocabulary"), staleTime: Infinity});
  const set = <K extends keyof Profile>(k: K, v: Profile[K]) => onChange({...value, [k]: v});
  const names = vocab.data;
  const needles = [norm(q), ...Object.entries(names?.aliases ?? {}).filter(([alias]) => norm(alias).includes(norm(q))).map(([, canonical]) => norm(canonical))];
  const options = names ? [
    ...names.substances.map((s) => ({code: `mnn:${s}`, name: s, context: "МНН"})),
    ...names.trade_names.map((s) => ({code: `trade:${s.name}`, name: s.name, context: `Торговое название · ${s.substances.join(", ")}`})),
    ...names.excipients.map((s) => ({code: `exc:${s}`, name: s, context: "Вспомогательное вещество"})),
  ].filter((o) => q.trim() && needles.some((n) => norm(o.name).includes(n))).slice(0, 30) : [];
  const listKeys = ["allergies", "past_intolerance", "intolerant_excipients"] as const;
  return <section className="card pad"><h2>Карточка пациента</h2>
    <div className="visit-fields">
      {([ ["pregnant", "Беременность"], ["lactating", "Лактация"], ["substance_use", "Употребление ПАВ в анамнезе / Употребление ПАВ активное"] ] as const).map(([k, label]) =>
        <label className="fld" key={k}>{label}<select className="inp" value={value[k]} onChange={(e) => set(k, e.target.value as Profile[typeof k])}>
          <option value="unknown">Неизвестно</option><option value="no">Нет</option><option value="yes">Да</option>
        </select></label>)}
    </div>
    <h3>Сопутствующие диагнозы</h3>
    {value.diagnoses.filter((code) => code !== main).map((code) => <div key={code}>{code} <button className="linkbtn" onClick={() => set("diagnoses", value.diagnoses.filter((c) => c !== code))}>Удалить</button></div>)}
    <DiagnosisSearch value="" scope="contra" onChange={(code) => set("diagnoses", [...new Set([...value.diagnoses, code])])} />
    <h3>Аллергии и непереносимость</h3>
    <label className="fld">Тип записи<select className="inp" value={fact} onChange={(e) => setFact(e.target.value as typeof fact)}><option value="past_intolerance">Непереносимость</option><option value="allergies">Аллергия</option></select></label>
    <SearchSelect label="МНН, торговое название, вспомогательное вещество или синоним" value={q} onQuery={setQ} options={options}
      onPick={(o) => { const k = fact === "allergies" ? fact : o.code.startsWith("exc:") ? "intolerant_excipients" : fact; set(k, [...new Set([...(value[k] ?? []), o.name])]); setQ(""); }} />
    {vocab.isError && <ErrorBox error={vocab.error} />}
    {listKeys.map((k) => <div key={k} className="q"><b>{k === "allergies" ? "Аллергии" : k === "past_intolerance" ? "Непереносимость препаратов" : "Непереносимость вспомогательных веществ"}</b>
      {value[k] === null ? <span>Не уточнено</span> : value[k]?.length === 0 ? <span>Отрицает</span> : value[k]?.map((s) => <div key={s}>{s} <button className="linkbtn" onClick={() => set(k, value[k]?.filter((x) => x !== s) ?? null)}>Удалить</button></div>)}
      <div><button className="linkbtn" onClick={() => set(k, [])}>Отрицает</button> · <button className="linkbtn" onClick={() => set(k, null)}>Не уточнено</button></div>
    </div>)}
    <p className="sub">Выбор торгового названия сохраняет непереносимость именно этого препарата. Вспомогательные вещества выбираются отдельно.</p>
  </section>;
}
