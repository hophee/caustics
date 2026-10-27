/**
 * Board K-Scales: the scale catalogue (`GET /scales`) and the selected scale's items with their answer
 * options (`GET /scales/{code}`). Score bands, critical items, sources and the per-diagnosis map are
 * not served by the API yet: not shown.
 */
import { useQuery } from "@tanstack/react-query";
import { useSearchParams } from "react-router";
import { api } from "../../../api/client";
import type { ScaleForm, ScaleListItem } from "../../../api/types";
import { plural } from "../../../lib/format";
import { useScreen } from "../../../ui/screen";
import { Loading, Q } from "../../../ui/states";
import { FILLED_BY, scaleTitle } from "./scalesModel";
import "./Scales.css";

function ScaleCard({ code }: { code: string }) {
  const form = useQuery({
    queryKey: ["kb", "scale", code],
    queryFn: () => api.get<ScaleForm>(`/scales/${encodeURIComponent(code)}`),
    staleTime: Infinity,
  });
  return (
    <section className="card">
      <Q query={form} loading={<div className="pad"><Loading /></div>}>
        {(scale) => {
          const title = scaleTitle(scale.name, scale.code);
          return (
            <>
              <div className="ch">
                <div>
                  <div className="lt">{scale.code}</div>
                  <div className="sub">
                    {[title, FILLED_BY[scale.filled_by].long, `${scale.items.length} ${plural(scale.items.length, "пункт", "пункта", "пунктов")}`, `версия ${scale.version}`]
                      .filter(Boolean)
                      .join(" · ")}
                  </div>
                </div>
              </div>
              <ol className="items">
                {scale.items.map((item, index) => (
                  <li key={item.id}>
                    <span className="no">{index + 1}</span>
                    <div>
                      <div>{item.text}</div>
                      <div className="opts">
                        {item.options.map((option) => (
                          <span className="chip" key={option.value}>
                            <b>{option.value}</b> {option.label}
                          </span>
                        ))}
                      </div>
                    </div>
                  </li>
                ))}
              </ol>
            </>
          );
        }}
      </Q>
    </section>
  );
}

export function KnowledgeScales() {
  useScreen("k-scales");
  const [params, setParams] = useSearchParams();
  const scales = useQuery({
    queryKey: ["kb", "scales"],
    queryFn: () => api.get<ScaleListItem[]>("/scales"),
    staleTime: Infinity,
  });
  const selected = params.get("scale") ?? scales.data?.[0]?.code ?? null;

  return (
    <main className="sheet">
      <h1>Шкалы</h1>
      <Q query={scales}>
        {(list) => (
          <div className="cols">
            <section className="card">
              <div className="scroll">
                <table>
                  <thead>
                    <tr>
                      <th>Шкала</th>
                      <th>Заполняет</th>
                      <th className="num">Пунктов</th>
                    </tr>
                  </thead>
                  <tbody>
                    {list.map((scale) => (
                      <tr
                        key={scale.code}
                        className={scale.code === selected ? "sel" : undefined}
                        aria-selected={scale.code === selected}
                        tabIndex={0}
                        onClick={() => setParams({ scale: scale.code }, { replace: true })}
                        onKeyDown={(event) => {
                          if (event.key === "Enter" || event.key === " ") {
                            event.preventDefault();
                            setParams({ scale: scale.code }, { replace: true });
                          }
                        }}
                      >
                        <td style={{ fontWeight: 500 }}>
                          {scale.code}
                          <span className="d2">{scaleTitle(scale.name, scale.code)}</span>
                        </td>
                        <td>{FILLED_BY[scale.filled_by].short}</td>
                        <td className="num">{scale.items}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </section>
            {selected && <ScaleCard code={selected} />}
          </div>
        )}
      </Q>
    </main>
  );
}
