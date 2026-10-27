import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import type { Visit } from "../../api/types";
import { longDay } from "../../lib/format";
import { useScreen } from "../../ui/screen";
import { Q } from "../../ui/states";
import { visitFields } from "../doctor/patient/parts/visits";

export function PatientVisits() {
  useScreen("p-visits");
  const visits = useQuery({ queryKey: ["me", "visits"], queryFn: () => api.get<Visit[]>("/me/visits") });
  return <main className="wrap narrow"><h1>Приёмы</h1><Q query={visits}>{(list) => <>
    {!list.length && <p>Сохранённых приёмов пока нет</p>}
    {list.map((v) => <section className="card" key={v.visit_id}><h2>{longDay(v.held_at)}</h2>
      <dl>{visitFields(v.content ?? {}).map((f) => <div key={f.label}><dt><b>{f.label}</b></dt><dd style={{ whiteSpace: "pre-wrap", margin: "4px 0 16px" }}>{f.value}</dd></div>)}</dl>
    </section>)}
  </>}</Q></main>;
}
