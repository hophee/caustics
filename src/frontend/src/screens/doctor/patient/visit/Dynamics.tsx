import { Link } from "react-router";
import { useCasePeriod, useProgress, useResults } from "../parts/data";
import { useCurrent, usePrescriptions } from "../therapy/api";
import { normalizeRegimen } from "../therapy/regimen";
import { ScaleCard } from "../Scales";
import { ErrorBox, Loading } from "../../../../ui/states";
import { criterionFor, evaluateCourse, therapyStart } from "./efficacy";
import { addDays } from "../parts/period";
export function Dynamics({patientId, diagnosis}: {patientId: string; diagnosis: string}) {
  const progress = useProgress(patientId); const results = useResults(patientId); const period = useCasePeriod(patientId);
  const therapy = useCurrent(patientId); const versions = usePrescriptions(patientId);
  if (progress.isPending || results.isPending || !period) return <Loading />;
  if (progress.isError || results.isError) return <ErrorBox error={progress.error ?? results.error} />;
  const starts = (therapy.data?.items ?? []).map((i) => normalizeRegimen(i.regimen)?.start).filter((s): s is string => Boolean(s)).sort();
  const start = therapyStart(versions.data?.items ?? []) ?? starts.at(-1); const rule = criterionFor(diagnosis);
  const measures = rule ? results.data.items.filter((r) => r.scale_code === rule.scale && r.status === "complete" && typeof r.total === "number")
    .map((r) => ({day: r.taken_at.slice(0,10), value: r.total as number})) : [];
  const assessment = rule ? evaluateCourse(rule, start, period.end, measures) : null;
  return <section className="card pad"><h2>Динамика и эффективность терапии</h2>
    <p>Начало текущего курса: {start || "не указано"}. Последнее изменение назначения: {versions.data?.items[0]?.confirmed_at.slice(0,10) || "нет данных"}.</p>
    {assessment && <div className={assessment.kind === "review" ? "notice" : "q"}><p>{assessment.text}</p>
      <p>{rule?.text}</p><small>{rule?.source}</small>
      {assessment.kind === "missing" && <Link to={`/d/patients/${patientId}/visit?step=tests`}>Пройти шкалу</Link>}
      {assessment.kind === "review" && <Link to={`/d/patients/${patientId}/visit?step=decision`}>Открыть решение по терапии</Link>}
    </div>}
    {!progress.data.length && <p>Результатов шкал пока нет.</p>}
    <div className="charts">{progress.data.map((series) => <ScaleCard key={`${series.scale_code}/${series.subscale_code}`} series={series} results={results.data.items} period={period}
      therapy={start && rule && series.scale_code === rule.scale && series.subscale_code === null ? {start,
        from: addDays(start, rule.scale === "HDRS-17" ? 21 : rule.earliestDay), until: addDays(start, rule.scale === "HDRS-17" ? 28 : rule.scale === "CAPS" ? 84 : 42),
        baseline: assessment?.baseline?.value, reference: rule.reduction !== undefined && assessment?.baseline ? assessment.baseline.value * (1-rule.reduction/100) : undefined} : undefined} />)}</div>
    <p className="sub">Для шкал без заданного в КР критерия показана динамика баллов. Она не является автоматическим заключением об эффективности. Эти графики доступны врачу.</p>
  </section>;
}
