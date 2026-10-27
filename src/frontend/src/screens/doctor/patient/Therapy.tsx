/**
 * «Терапия» tab of the patient card (board I2-Therapy): current prescription with live warnings and
 * their justifications, what the patient sees, version history, data to clarify, adherence.
 */
import { useMemo, useState } from "react";
import { Link, useParams } from "react-router";
import type { AdherenceSummary } from "../../../api/types";
import { dayMonth, plural, when } from "../../../lib/format";
import { Lv } from "../../../ui/levels";
import { useScreen } from "../../../ui/screen";
import { usePatientSummary } from "../../../api/queries";
import { ErrorBox, Loading } from "../../../ui/states";
import { useAdherence, useCheck, useCurrent, usePrescriptions, type AdherenceOut, type PrescriptionVersion } from "./therapy/api";
import { labelText } from "../../../lib/labelText";
import { itemTitle, joinCurrent, justifiedIn, labelNames, type CurrentView } from "./therapy/current";
import { bySeverity, cardCheck } from "./therapy/model";
import { WarningLine } from "./therapy/parts";
import { courseText, doseText, normalizeRegimen, periodText } from "./therapy/regimen";
import { shortSource } from "./therapy/selection";
import "./therapy/therapy.css";

const EMPTY_DRAFT_OUT = { new_items: [], stopped: [] };

export function Therapy() {
  useScreen("therapy");
  const { patientId = "" } = useParams();
  const current = useCurrent(patientId);
  const summary = usePatientSummary(patientId);
  const female = summary.data?.sex === "female";
  const [limit, setLimit] = useState(20);
  const versions = usePrescriptions(patientId, limit);
  const live = useCheck(patientId, EMPTY_DRAFT_OUT, current.data?.base_token, (current.data?.items.length ?? 0) > 0);
  const [openVersion, setOpenVersion] = useState<string | null>(null);

  const list = useMemo(() => versions.data?.items ?? [], [versions.data]);
  const views = useMemo(() => joinCurrent(current.data?.items ?? [], list), [current.data, list]);
  const names = useMemo(() => labelNames(list), [list]);
  const justified = useMemo(() => justifiedIn(list), [list]);
  const latest = list[0];

  if (current.isPending || versions.isPending) return <Loading />;
  if (current.isError) return <ErrorBox error={current.error} onRetry={() => void current.refetch()} />;
  if (versions.isError) return <ErrorBox error={versions.error} onRetry={() => void versions.refetch()} />;

  const toClarify = (live.data?.warnings ?? []).filter((w) => w.kind === "missing_patient_data");
  const draftUrl = `/d/patients/${patientId}/draft`;

  return (
    <div className="cols">
      <div className="col">
        <section className="card">
          <div className="ch">
            <div>
              <h2>Текущее назначение</h2>
              {latest && (
                <div className="sub">
                  версия {latest.version} · {when(latest.confirmed_at)}
                </div>
              )}
            </div>
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
              <Link className="btn btn-t" to={`${draftUrl}?pick=1`}>
                Добавить препарат
              </Link>
              <Link className="btn btn-p" to={draftUrl}>
                Изменить назначение
              </Link>
            </div>
          </div>
          {views.length === 0 && <div className="pad">Ничего не принимает</div>}
          {live.isError && (
            <div className="pad">
              <ErrorBox error={live.error} onRetry={() => void live.refetch()} />
            </div>
          )}
          {views.map((view) => (
            <CurrentDrug
              key={view.ref}
              view={view}
              warnings={cardCheck(live.data, view.key).warnings}
              checking={live.isPending}
              names={names}
              justified={justified}
              onOpenVersion={(id) => setOpenVersion(id)}
            />
          ))}
        </section>
        {views.length > 0 && latest && (
          <section className="card">
            <div className="ch">
              <h2>{female ? "Видит пациентка" : "Видит пациент"}</h2>
              <span className="sub">с {when(latest.confirmed_at)}</span>
            </div>
            <div className="pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {views
                .filter((v) => v.item)
                .map((v) => {
                  const r = normalizeRegimen(v.regimen);
                  return (
                    <div className="pv" key={v.ref}>
                      <div style={{ fontWeight: 500 }}>
                        {v.item?.trade_name}, {v.item?.form}
                      </div>
                      <div className="sub">{[doseText(r), periodText(r)].filter(Boolean).join(" · ")}</div>
                    </div>
                  );
                })}
            </div>
          </section>
        )}
        <section className="card">
          <div className="ch">
            <h2>История назначений</h2>
            <span className="sub">
              {versions.data.total} {plural(versions.data.total, "версия", "версии", "версий")}
            </span>
          </div>
          {list.length === 0 && <div className="pad">Назначений нет</div>}
          {list.map((v) => (
            <Version
              key={v.prescription_id}
              version={v}
              open={openVersion === v.prescription_id}
              onToggle={() => setOpenVersion((id) => (id === v.prescription_id ? null : v.prescription_id))}
              names={names}
            />
          ))}
          {versions.data.total > list.length && (
            <div className="pad">
              <button type="button" className="linkbtn" onClick={() => setLimit((n) => n + 20)}>
                Показать ещё
              </button>
            </div>
          )}
        </section>
      </div>
      <div className="col">
        {toClarify.length > 0 && (
          <section className="card">
            <div className="ch">
              <h2>{female ? "Уточнить у пациентки" : "Уточнить у пациента"}</h2>
              <span className="sub">{toClarify.length}</span>
            </div>
            <div className="pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
              {toClarify.map((w) => (
                <WarningLine key={w.id} warning={w} names={names} />
              ))}
            </div>
          </section>
        )}
        <AdherenceCard patientId={patientId} />
      </div>
    </div>
  );
}

function CurrentDrug({
  view,
  warnings,
  checking,
  names,
  justified,
  onOpenVersion,
}: {
  view: CurrentView;
  warnings: ReturnType<typeof cardCheck>["warnings"];
  checking: boolean;
  names: Map<string, string>;
  justified: ReturnType<typeof justifiedIn>;
  onOpenVersion: (id: string) => void;
}) {
  const r = normalizeRegimen(view.regimen);
  const row = view.item?.provenance?.row;
  return (
    <>
      <div className="drug">
        <div>
          <div style={{ fontWeight: 500 }}>{itemTitle(view)}</div>
          <div className="sub">
            {[view.item?.substance ?? view.substance, row ? `КР: ${row.window.toLowerCase()}` : null, view.patientReported ? "со слов пациента" : null]
              .filter(Boolean)
              .join(" · ")}
          </div>
        </div>
        <div>
          <div>{doseText(r)}</div>
          <div className="sub">{courseText(r)}</div>
        </div>
        {view.labelId ? (
          <Link to={`/d/kb/drugs/${view.labelId}`} className="sub">
            ОХЛП
          </Link>
        ) : (
          <span />
        )}
      </div>
      {(warnings.length > 0 || checking) && (
        <div className="warns">
          {checking && <span className="sub">Проверка…</span>}
          {[...warnings].sort(bySeverity).map((w) => {
            const j = justified.get(w.id);
            return (
              <WarningLine
                key={w.id}
                warning={w}
                names={names}
                extra={
                  j && (
                    <>
                      {" · обоснование "}
                      {dayMonth(j.version.confirmed_at)}
                      {" · "}
                      <button type="button" className="srcbtn" onClick={() => onOpenVersion(j.version.prescription_id)}>
                        открыть
                      </button>
                    </>
                  )
                }
              />
            );
          })}
        </div>
      )}
    </>
  );
}

function Version({
  version,
  open,
  onToggle,
  names,
}: {
  version: PrescriptionVersion;
  open: boolean;
  onToggle: () => void;
  names: Map<string, string>;
}) {
  const reds = version.warnings.filter((w) => w.severity === "contraindicated").length;
  const drugs = version.items.map((i) => {
    const r = normalizeRegimen(i.regimen);
    return `${i.trade_name.toLowerCase()} ${r?.intakes.map((x) => `${x.amount} ${x.unit}`).join(" + ") ?? ""}`.trim();
  });
  const sub = [
    version.justifications.length
      ? `${version.justifications.length} ${plural(version.justifications.length, "противопоказание", "противопоказания", "противопоказаний")} с обоснованием`
      : null,
    version.stopped_course_ids.length ? `отменено: ${version.stopped_course_ids.length}` : null,
    !version.justifications.length && reds ? `${reds} ${plural(reds, "противопоказание", "противопоказания", "противопоказаний")}` : null,
  ].filter(Boolean);
  return (
    <>
      <div className="ver">
        <span style={{ fontWeight: 500 }}>{dayMonth(version.confirmed_at)}</span>
        <div>
          Версия {version.version} · {drugs.length ? drugs.join(", ") : "без препаратов"}
          {sub.length > 0 && <div className="sub">{sub.join(" · ")}</div>}
        </div>
        <button type="button" className="srcbtn" aria-expanded={open} onClick={onToggle}>
          {open ? "Скрыть" : "Открыть"}
        </button>
      </div>
      {open && (
        <div className="pad" style={{ display: "flex", flexDirection: "column", gap: 12, background: "#fbfaf8" }}>
          {version.items.map((i) => {
            const r = normalizeRegimen(i.regimen);
            const row = i.provenance?.row;
            return (
              <div key={i.course_id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <div style={{ fontWeight: 500 }}>
                  {i.trade_name} · {i.form} {!i.is_new && <span className="tag">продолжено</span>}
                </div>
                <div>{[doseText(r), courseText(r)].filter(Boolean).join(" · ")}</div>
                {row && (
                  <div className="sub">
                    {shortSource(row.source)} · {row.window}
                    {row.evidence ? ` · УУР/УДД ${row.evidence.replace(/\s/g, "")}` : ""}
                  </div>
                )}
                {i.suggested_dose_text && (
                  <div className="sub">
                    ОХЛП: «{labelText(i.suggested_dose_text)}»
                  </div>
                )}
              </div>
            );
          })}
          {version.warnings.length > 0 && (
            <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
              <h3>Предупреждения при подтверждении</h3>
              {[...version.warnings].sort(bySeverity).map((w) => {
                const j = version.justifications.find((x) => x.warning_id === w.id);
                return (
                  <div key={w.id} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                    <WarningLine warning={w} names={names} />
                    {j && (
                      <div className="sub" style={{ paddingLeft: 12 }}>
                        Обоснование: {j.text}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>
          )}
          <div className="sub">
            {when(version.confirmed_at)} · база знаний {version.knowledge_version}
          </div>
        </div>
      )}
    </>
  );
}

const BAND: Record<NonNullable<AdherenceSummary["band"]>, string> = { green: "lv-grn", yellow: "lv-amb", red: "lv-red" };

function SummaryLines({ s, labels }: { s: AdherenceSummary; labels: Record<string, string> }) {
  const reasons = Object.entries(s.skip_reasons).filter(([, n]) => n > 0);
  return (
    <>
      <div className="cnt">
        <span>Вовремя</span>
        <b style={{ fontWeight: 500 }}>{s.on_time}</b>
      </div>
      <div className="cnt">
        <span>С опозданием</span>
        <b style={{ fontWeight: 500 }}>{s.late}</b>
      </div>
      <div className="cnt">
        <span>Пропущено</span>
        <b style={{ fontWeight: 500 }}>{s.skipped}</b>
      </div>
      <div className="cnt">
        <span>Без отметки</span>
        <b style={{ fontWeight: 500 }}>{s.unmarked}</b>
      </div>
      {reasons.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <h3>Причины пропусков</h3>
          {reasons.map(([code, n]) => (
            <div className="cnt" key={code}>
              <span>{labels[code] ?? code}</span>
              <b style={{ fontWeight: 500 }}>{n}</b>
            </div>
          ))}
        </div>
      )}
    </>
  );
}

function AdherenceCard({ patientId }: { patientId: string }) {
  const adherence = useAdherence(patientId);
  return (
    <section className="card">
      <div className="ch">
        <h2>Приём лекарства</h2>
        {adherence.data && (
          <span className="sub">
            {dayMonth(adherence.data.since)} — {dayMonth(adherence.data.until)}
          </span>
        )}
      </div>
      <div className="pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
        {adherence.isPending && <Loading />}
        {adherence.isError && <ErrorBox error={adherence.error} onRetry={() => void adherence.refetch()} />}
        {adherence.data && <AdherenceBody data={adherence.data} />}
      </div>
    </section>
  );
}

function AdherenceBody({ data }: { data: AdherenceOut }) {
  const s = data.summary;
  if (s.expected === 0) return <div>Отметок за период нет</div>;
  const taken = s.on_time + s.late;
  const countable = taken + s.skipped + (data.unmarked_counts_as_missed ? s.unmarked : 0);
  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
        <span style={{ font: "600 22px/28px 'Literata',serif" }}>{s.percent === null ? "—" : `${Math.round(s.percent)}%`}</span>
        {s.band && <Lv cls={BAND[s.band]}>{taken} из {countable}</Lv>}
      </div>
      <SummaryLines s={s} labels={data.skip_reason_labels} />
      {data.courses.length > 1 &&
        data.courses.map((c) => (
          <div key={c.course_id} className="cnt">
            <span>{c.name}</span>
            <b style={{ fontWeight: 500 }}>{c.summary.percent === null ? "—" : `${Math.round(c.summary.percent)}%`}</b>
          </div>
        ))}
    </>
  );
}

