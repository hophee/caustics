import { useState } from "react";
import type { Investigation } from "./model";
import { investigation, investigationCatalog } from "./investigationCatalog";
import { SearchSelect } from "./SearchSelect";
export function Investigations({items, onChange}: {items: Investigation[]; onChange: (items: Investigation[]) => void}) {
  const [q, setQ] = useState("");
  const update = (i: number, patch: Partial<Investigation>) => onChange(items.map((x, j) => i === j ? {...x, ...patch} : x));
  return <section className="card pad"><h2>Анализы и исследования</h2>
    <p className="sub">Назначения редактирует врач. Автоматический фильтр «Анализы, ЭКГ» в прототипе не применяется.</p>
    {items.map((item, i) => <fieldset key={`${i}-${item.name}`}><legend>{item.name}</legend>
      {item.condition && <p><b>Условие:</b> {item.condition}</p>}<small>{item.source}</small>
      <div className="visit-fields"><label className="fld">Срок / дата выполнения<input className="inp" type="date" value={item.due} onChange={(e) => update(i, {due: e.target.value})} /></label>
        <label className="fld">Результат<textarea className="inp" value={item.result} onChange={(e) => update(i, {result: e.target.value})} /></label></div>
      <button className="linkbtn" onClick={() => onChange(items.filter((_, j) => i !== j))}>Удалить из назначения</button>
    </fieldset>)}
    <SearchSelect label="Добавить исследование из полного списка" value={q} onQuery={setQ}
      options={investigationCatalog.filter((n) => n.toLowerCase().includes(q.toLowerCase()) && !items.some((i) => i.name === n)).map((n) => ({code: n, name: n}))}
      onPick={(o) => {onChange([...items, investigation(o.name, "Назначено врачом")]); setQ("");}} />
  </section>;
}
