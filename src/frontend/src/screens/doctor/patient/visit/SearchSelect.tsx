import { useId, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../../../api/client";

export interface SearchOption { code: string; name: string; context?: string }
export function SearchSelect({ label, value, options, onQuery, onPick, showCode = true }: {
  label: string; value: string; options: SearchOption[]; onQuery: (q: string) => void; onPick: (o: SearchOption) => void;
  showCode?: boolean;
}) {
  const id = useId();
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const pick = (o: SearchOption) => { onPick(o); setOpen(false); };
  return <div className="fld visit-search"><label htmlFor={id}>{label}</label>
    <input id={id} className="inp" role="combobox" aria-expanded={open} aria-controls={`${id}-options`} aria-autocomplete="list"
      value={value} onFocus={() => setOpen(true)} onBlur={() => setOpen(false)}
      onChange={(e) => { onQuery(e.target.value); setOpen(true); setActive(0); }}
      onKeyDown={(e) => {
        if (e.key === "Escape") setOpen(false);
        if (e.key === "ArrowDown") { e.preventDefault(); setOpen(true); setActive((i) => Math.min(i + 1, options.length - 1)); }
        if (e.key === "ArrowUp") { e.preventDefault(); setActive((i) => Math.max(0, i - 1)); }
        if (e.key === "Enter") { e.preventDefault(); if (open && options[active]) pick(options[active]); }
      }} />
    {open && <ul id={`${id}-options`} role="listbox" className="visit-options">
      {options.map((o, i) => <li key={o.code} role="option" aria-selected={i === active}>
        <button type="button" onMouseDown={(e) => e.preventDefault()} onClick={() => pick(o)} title={o.context}>
          {showCode && o.code !== o.name && <b>{o.code} · </b>}{o.name}
        </button>
      </li>)}
      {!options.length && <li>Совпадений нет</li>}
    </ul>}
  </div>;
}

export function DiagnosisSearch({ value, onChange, scope = "main", onClear }: {
  value: string; onChange: (code: string) => void; scope?: "main" | "contra"; onClear?: () => void;
}) {
  const [query, setQuery] = useState(value);
  const results = useQuery({ queryKey: ["kb", "diagnosis-search", scope, query],
    queryFn: () => api.get<SearchOption[]>("/knowledge/diagnoses", { q: query, scope }), staleTime: 60_000 });
  return <><SearchSelect label="Поиск по диагнозу или коду МКБ" value={query} options={results.data ?? []}
    onQuery={(q) => { setQuery(q); if (!q) onClear?.(); }}
    onPick={(o) => { setQuery(`${o.code} · ${o.name}`); onChange(o.code); }} />
    {query && <button type="button" className="linkbtn" onClick={() => { setQuery(""); onClear?.(); }}>Очистить поиск</button>}
    {results.isError && <p role="alert">Не удалось загрузить справочник</p>}
  </>;
}
