/** «Анкеты» (board P-Forms): questionnaires due now and the patient's own history with scores and key-based interpretations. */
import { useState } from "react";
import { Link } from "react-router";
import { dayMonthLong } from "../../lib/format";
import { useScreen } from "../../ui/screen";
import { ErrorBox, Loading } from "../../ui/states";
import { assignmentLine } from "./questionnaireFlow";
import { useAssignments, useMyResponses, useScales } from "./patientApi";
import "./patient.css";

const PAGE = 20;

export function Forms() {
  useScreen("p-forms");
  const assignments = useAssignments();
  const scales = useScales();
  const [limit, setLimit] = useState(PAGE);
  const history = useMyResponses(limit);
  const scaleOf = (code: string) => scales.data?.find((s) => s.code === code);

  return (
    <main className="wrap narrow">
      <h1>Анкеты</h1>
      <div className="col">
        {assignments.isPending && <Loading />}
        {assignments.isError && <ErrorBox error={assignments.error} onRetry={() => void assignments.refetch()} />}
        {(assignments.data ?? []).map((a) => (
          <section key={a.assignment_id} className="card due">
            <div>
              <h2>{scaleOf(a.scale_code)?.name ?? a.scale_code}</h2>
              <div className="sub">{assignmentLine(a, scaleOf(a.scale_code))}</div>
            </div>
            <Link className="btn btn-p" to={`/p/forms/${a.assignment_id}`} style={{ flex: "none" }}>
              Пройти
            </Link>
          </section>
        ))}
        <section className="card">
          <h2>Мои анкеты</h2>
          {history.isPending && <Loading />}
          {history.isError && <ErrorBox error={history.error} onRetry={() => void history.refetch()} />}
          {history.data && history.data.items.length === 0 && (
            <div className="sub" style={{ marginTop: 8 }}>
              Анкет пока нет
            </div>
          )}
          {history.data && history.data.items.length > 0 && (
            <div className="hist" style={{ marginTop: 8 }}>
              {history.data.items.map((r) => (
                <div key={r.response_id} className="hr">
                  <div>
                    <div style={{ fontWeight: 500 }}>{r.scale_name}</div>
                    <div className="sub">{dayMonthLong(r.taken_on)}</div>
                  </div>
                  <div>
                    {r.total !== null && <div>Результат: {r.total}</div>}
                    {r.band?.label != null && <div>{String(r.band.label)}</div>}
                    {r.subscores.map((s, i) => (
                      <div key={i}>
                        {String(s.name ?? s.code ?? "")}: {String(s.value ?? "")}
                        {" · "}{String((s.band as Record<string, unknown> | null)?.label ?? "")}
                      </div>
                    ))}
                    {r.total === null && !r.band && !r.subscores.length && <div>Ответы сохранены, числовой результат не предусмотрен</div>}
                  </div>
                </div>
              ))}
            </div>
          )}
          {history.data && history.data.total > history.data.items.length && (
            <button type="button" className="btn btn-t more-btn" disabled={history.isFetching} onClick={() => setLimit((l) => l + PAGE)}>
              Показать ещё
            </button>
          )}
        </section>
      </div>
    </main>
  );
}
