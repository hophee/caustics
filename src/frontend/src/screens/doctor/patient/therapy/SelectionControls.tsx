import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../../../api/client";
import { ErrorBox, Loading } from "../../../../ui/states";

export interface SelectionAnswers { choice?: string; flags: string[] }
export interface SelectionForm {
  kind: "stage" | "history" | "line";
  label: string;
  button: string;
  choices: string[];
  default: string;
  flags: string[];
}

export function SelectionControls({ diagnosis, value, onChange, expanded = false }: {
  diagnosis: string; value: SelectionAnswers; onChange: (value: SelectionAnswers) => void; expanded?: boolean;
}) {
  const [open, setOpen] = useState(expanded);
  const form = useQuery({
    queryKey: ["kb", "selection-form", diagnosis, value.choice],
    queryFn: () => api.get<SelectionForm>("/guidelines/selection-form", { diagnosis, choice: value.choice }),
    enabled: Boolean(diagnosis),
  });
  if (form.isPending) return <Loading />;
  if (form.isError) return <ErrorBox error={form.error} />;
  const data = form.data;
  const selected = value.choice ?? data.default;
  return <section className="card pad" aria-label="Условия подбора">
    <h2>{data.label}</h2>
    {(open || expanded) ? <select className="inp" aria-label={data.label} value={selected}
      onChange={(e) => onChange({ choice: e.target.value, flags: [] })}>
      {data.kind === "line" && <option value="">Все линии</option>}
      {data.choices.map((choice) => <option key={choice} value={choice}>{choice}</option>)}
    </select> : <><p>{selected || "Все линии"}</p><button className="btn btn-t" type="button" onClick={() => setOpen(true)}>{data.button}</button></>}
    {data.flags.length > 0 && <fieldset><legend>Показания и условия</legend>
      {data.flags.map((flag) => <label key={flag} style={{ display: "block", margin: "8px 0" }}>
        <input type="checkbox" checked={value.flags.includes(flag)} onChange={(e) => onChange({ ...value,
          flags: e.target.checked ? [...value.flags, flag] : value.flags.filter((x) => x !== flag),
        })} /> {flag}
      </label>)}
    </fieldset>}
  </section>;
}
