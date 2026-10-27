/**
 * Drug picker (boards I2-PickEmpty / I2-PickSearch). Lists the КР candidates for the doctor's answers
 * or searches the whole ОХЛП base. Every listed label is checked against this patient and the current
 * draft (one combined probe check); the selected label is re-checked alone and shows every warning
 * with its sources. Filters only narrow the list: a contraindicated drug stays listed with its pill.
 */
import { useMemo, useState } from "react";
import type { Candidate, Warning } from "../../../../api/types";
import { CloseButton, Dialog } from "../../../../ui/Dialog";
import { Lv, SEVERITY } from "../../../../ui/levels";
import { ErrorBox, Loading } from "../../../../ui/states";
import { useCheck, useDrugSearch, useLabel, useSelection, type SearchHit } from "./api";
import { useDebounced } from "./hooks";
import { cardCheck, cleanQuote, isoToday, probeWarnings, shortCause, withProbes, type DraftOut, type Origin } from "./model";
import { SourceQuotes, WarningLine } from "./parts";
import { candidateIndex, groupCandidates, highlight, shortSource } from "./selection";
import "./Picker.css";
import { SelectionControls, type SelectionAnswers } from "./SelectionControls";
import { useStored } from "./store";
const EMPTY_ANSWERS: SelectionAnswers = {flags: []};
const isAnswers = (v: unknown): v is SelectionAnswers => typeof v === "object" && v !== null && Array.isArray((v as SelectionAnswers).flags);

export interface PickedLabel {
  labelId: string;
  title: string;
  origin: Origin;
  context: string;
}

interface Props {
  patientId: string;
  icd: string;
  patientName: string;
  suicideRisk: string;
  currentNames: string[];
  base: DraftOut;
  baseToken: string;
  draftCount: number;
  female: boolean;
  onAdd: (picked: PickedLabel) => void;
  onClose: () => void;
  expanded?: boolean;
  selectionKey?: string;
}

interface LabelRow {
  labelId: string;
  title: string;
  trade_name: string;
  form: string;
  substances: string[];
  atc: string | null;
  candidate: Candidate | null;
}

function fromCandidate(c: Candidate): LabelRow {
  return {
    labelId: c.label.id,
    title: c.label.display_name ?? `${c.label.trade_name}, ${c.label.form}`,
    trade_name: c.label.trade_name,
    form: c.label.form,
    substances: c.label.substances,
    atc: c.label.atc,
    candidate: c,
  };
}

function fromHit(h: SearchHit, candidate: Candidate | undefined): LabelRow {
  return { labelId: h.label_id, title: h.display_name, trade_name: h.trade_name, form: h.form, substances: h.substances, atc: h.atc, candidate: candidate ?? null };
}

function Pill({ warnings, pending }: { warnings: Warning[] | undefined; pending: boolean }) {
  if (!warnings) return <span className="chk">{pending ? "проверка…" : "проверка при выборе"}</span>;
  const top = warnings[0];
  if (!top) return <span className="chk">без предупреждений</span>;
  return <Lv cls={SEVERITY[top.severity].cls}>{shortCause(top)}</Lv>;
}

function Marked({ text, q }: { text: string; q: string }) {
  const [a, b, c] = highlight(text, q);
  return (
    <>
      {a}
      {b && <mark>{b}</mark>}
      {c}
    </>
  );
}

export function Picker(props: Props) {
  const { patientId, icd, patientName, base, onAdd, onClose } = props;
  const [q, setQ] = useState("");
  const [filters, setFilters] = useState({atc: "", form: "", mnn: "", offset: 0});
  const [picked, setPicked] = useState<LabelRow | null>(null);
  const [answers, setAnswers] = useStored<SelectionAnswers>(`kompas.selection.${patientId}.${icd}.${props.selectionKey ?? "card"}`, EMPTY_ANSWERS, isAnswers);
  const today = isoToday(); const query = useDebounced(q, 250);
  const selection = useSelection(patientId, answers);
  const index = useMemo(() => candidateIndex(selection.data), [selection.data]);
  const rowGroups = useMemo(() => groupCandidates(selection.data), [selection.data]);
  const search = useDrugSearch(query, filters);
  const allRows = useMemo(() => (search.data?.items ?? []).map((h) => fromHit(h, undefined)), [search.data]);
  const probeIds = useMemo(() => [...new Set([...index.keys(), ...allRows.map((r) => r.labelId)])], [index, allRows]);
  const probe = useMemo(() => withProbes(base, probeIds, today), [base, probeIds, today]);
  const probeCheck = useCheck(patientId, probe.draft, props.baseToken, probeIds.length > 0);
  const probeResult = useMemo(() => probeCheck.data ? probeWarnings(probeCheck.data, [...probe.keyOf.values()]) : null, [probeCheck.data, probe.keyOf]);
  const warningsOf = (id: string) => {const key = probe.keyOf.get(id); return key && probeResult ? probeResult.get(key) : undefined;};
  const excluded = (id: string) => warningsOf(id)?.some((w) => w.severity === "contraindicated") ?? false;
  const choose = (row: LabelRow) => setPicked(row);
  return <Dialog name="picksearch" className="visit-picker" onClose={onClose} labelledBy="pick-title">
    <div className="mh"><div><h1 id="pick-title">Подбор препаратов</h1><div className="sub">{patientName} · {icd}</div></div><CloseButton onClick={onClose} /></div>
    <SelectionControls diagnosis={icd} value={answers} expanded={props.expanded} onChange={(v) => {setAnswers(v); setPicked(null);}} />
    <div className="therapy-panes">
      <section className="therapy-pane"><h2>С автоматическими фильтрами</h2>
        <p className="sub">Диагноз, этап или история лечения, показания и профиль пациента. Противопоказанные варианты исключаются после проверки.</p>
        <p className="sub">Анализы, ЭКГ — в прототипе не применяется.</p>
        {selection.isPending && <Loading />}{selection.isError && <ErrorBox error={selection.error} />}
        {rowGroups.map((g) => <section className="therapy-role" key={g.row.id}><h3>{g.title}</h3>
          {g.row.stage.includes("второго выбора") && <Lv cls="lv-yel">2-й выбор</Lv>}
          {g.row.condition && <p>{g.row.condition}</p>}
          {g.groups.map((m) => <details key={m.mnn}><summary>{m.mnn} · {m.candidates.filter((c) => !excluded(c.label.id)).length} вариантов</summary>
            {m.candidates.filter((c) => !excluded(c.label.id)).map((c) => <button type="button" className="therapy-label" key={c.label.id} onClick={() => choose(fromCandidate(c))}>
              <span>{c.label.display_name ?? c.label.trade_name}</span><Pill warnings={warningsOf(c.label.id)} pending={probeCheck.isFetching} />
            </button>)}
          </details>)}
          <details><summary>Основание КР</summary><blockquote>{g.row.quote}</blockquote><small>{g.row.source}</small></details>
        </section>)}
        {selection.data && !rowGroups.length && <p>Для выбранных условий вариантов нет.</p>}
        {selection.data?.restrictions.map((r, i) => <div className="notice" key={i}>{r.rule}<blockquote>{r.quote}</blockquote><small>{r.source}</small></div>)}
        {(selection.data?.restrictions_to_clarify as {rule: string; source: string}[] | undefined)?.map((r, i) => <p key={i}><b>Уточнить:</b> {r.rule} <small>{r.source}</small></p>)}
        {(selection.data?.advice as {text: string; row: {source: string}}[] | undefined)?.map((a, i) => <p key={i}>{a.text} <small>{a.row.source}</small></p>)}
      </section>
      <section className="therapy-pane"><h2>Полная база ОХЛП</h2>
        <p className="sub">Ручные фильтры. Проверки безопасности остаются доступными для каждого препарата.</p>
        <label className="fld">Поиск<input className="inp" placeholder="МНН, торговое название или АТХ" value={q} onChange={(e) => {setQ(e.target.value); setFilters((f) => ({...f, offset: 0}));}} /></label>
        <div className="visit-fields">{([ ["mnn", "МНН"], ["atc", "АТХ"], ["form", "Лекарственная форма"] ] as const).map(([key, label]) => <label key={key} className="fld">{label}<input className="inp" value={filters[key]} onChange={(e) => setFilters((f) => ({...f, [key]: e.target.value, offset: 0}))} /></label>)}</div>
        {search.isPending && <Loading />}{search.isError && <ErrorBox error={search.error} />}
        <LabelList rows={allRows} q={query} selected={picked?.labelId ?? null} onSelect={(id) => {const row = allRows.find((r) => r.labelId === id); if (row) choose(row);}} warningsOf={warningsOf} pending={probeCheck.isFetching} />
        <p>Найдено: {search.data?.total ?? 0}. Показаны {filters.offset + 1}–{filters.offset + allRows.length}.</p>
        <button className="btn btn-t" disabled={!filters.offset} onClick={() => setFilters((f) => ({...f, offset: Math.max(0, f.offset - 50)}))}>Назад</button>
        <button className="btn btn-t" disabled={filters.offset + 50 >= (search.data?.total ?? 0)} onClick={() => setFilters((f) => ({...f, offset: f.offset + 50}))}>Далее</button>
      </section>
    </div>
    {probeCheck.isError && <ErrorBox error={probeCheck.error} />}
    {picked && <Details patientId={patientId} labelId={picked.labelId} row={picked} candidate={picked.candidate} base={base} baseToken={props.baseToken} female={props.female} today={today} />}
    <div className="mf"><span>{props.draftCount ? `В аптечке: ${props.draftCount}` : "Выберите препарат в одном из окон"}</span>
      {picked && <AddButton labelId={picked.labelId} row={picked} candidate={picked.candidate} onAdd={onAdd} />}
      <button className="btn btn-t" onClick={onClose}>К аптечке</button>
    </div>
  </Dialog>;
}
function LabelList({
  rows,
  q,
  selected,
  onSelect,
  warningsOf,
  pending,
}: {
  rows: LabelRow[];
  q: string;
  selected: string | null;
  onSelect: (id: string) => void;
  warningsOf: (id: string) => Warning[] | undefined;
  pending: boolean;
}) {
  const groups = useMemo(() => {
    const map = new Map<string, LabelRow[]>();
    for (const r of rows) {
      const key = r.substances.join(" + ") || r.trade_name;
      map.set(key, [...(map.get(key) ?? []), r]);
    }
    return [...map.entries()];
  }, [rows]);
  return (
    <>
      {groups.map(([mnn, list]) => {
        const c = list.find((r) => r.candidate)?.candidate ?? null;
        const atc = list.find((r) => r.atc)?.atc;
        return (
          <div key={mnn} style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <div className="grp">
              <h2>
                <Marked text={mnn.charAt(0).toUpperCase() + mnn.slice(1)} q={q} />
              </h2>
              <span className="sub">{[c ? `рекомендован КР · ${c.row.window}` : null, atc].filter(Boolean).join(" · ")}</span>
            </div>
            <div>
              {list.map((r) => (
                <button
                  key={r.labelId}
                  type="button"
                  className={`row${r.labelId === selected ? " sel" : ""}`}
                  aria-pressed={r.labelId === selected}
                  onClick={() => onSelect(r.labelId)}
                >
                  <div>
                    <div style={{ fontWeight: 500 }}>
                      <Marked text={`${r.trade_name} · ${r.form}`} q={q} />
                    </div>
                    <div className="sub">{r.substances.join(" + ")}</div>
                  </div>
                  <Pill warnings={warningsOf(r.labelId)} pending={pending} />
                </button>
              ))}
            </div>
          </div>
        );
      })}
    </>
  );
}

function pickedOf(labelId: string, row: LabelRow | null, candidate: Candidate | null, title: string): PickedLabel {
  return {
    labelId,
    title,
    origin: candidate ? { kind: "kr_suggestion", row_id: candidate.row.id } : { kind: "search" },
    context: candidate
      ? `${shortSource(candidate.row.source)} · ${candidate.row.window}`
      : [row?.atc, "поиск по базе ОХЛП"].filter(Boolean).join(" · "),
  };
}

function AddButton({ labelId, row, candidate, onAdd }: { labelId: string; row: LabelRow | null; candidate: Candidate | null; onAdd: (p: PickedLabel) => void }) {
  const label = useLabel(labelId);
  const title = label.data?.display_name ?? row?.title;
  return (
    <button type="button" className="btn btn-p" disabled={!title} onClick={() => title && onAdd(pickedOf(labelId, row, candidate, title))}>
      Добавить в аптечку
    </button>
  );
}

function Details({
  patientId,
  labelId,
  row,
  candidate,
  base,
  baseToken,
  today,
  female,
}: {
  patientId: string;
  labelId: string;
  row: LabelRow | null;
  candidate: Candidate | null;
  base: DraftOut;
  baseToken: string;
  today: string;
  female: boolean;
}) {
  const label = useLabel(labelId);
  const single = useMemo(() => withProbes(base, [labelId], today), [base, labelId, today]);
  const check = useCheck(patientId, single.draft, baseToken);
  const warnings = check.data ? cardCheck(check.data, single.keyOf.get(labelId) ?? "").warnings : [];
  const l = label.data;
  const dose = l?.dose.start_text;
  const names = new Map(l ? [[l.id, l.trade_name]] : []);
  return (
    <aside className="det">
      <div>
        <div style={{ font: "600 18px/24px 'Literata',serif" }}>{l?.display_name ?? row?.title ?? "…"}</div>
        <div className="sub">{l ? [l.substances.join(" + "), l.atc].filter(Boolean).join(" · ") : ""}</div>
      </div>
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        {candidate && <Lv cls="lv-ok">Рекомендован КР</Lv>}
        {candidate?.off_label_in_kr && <Lv cls="lv-amb">Вне инструкции по КР</Lv>}
        {l?.needs_check && <Lv cls="lv-gry">Данные ОХЛП требуют проверки</Lv>}
      </div>
      <h3>{female ? "Для этой пациентки" : "Для этого пациента"}</h3>
      {check.isPending && <Loading label="Проверка…" />}
      {check.isError && <ErrorBox error={check.error} onRetry={() => void check.refetch()} />}
      {check.data && warnings.length === 0 && <span className="sub">Предупреждений нет</span>}
      {warnings.map((w) => (
        <WarningLine key={w.id} warning={w} names={names} />
      ))}
      <h3>Доза</h3>
      {label.isPending && <Loading />}
      {l && (dose?.quote || dose?.value) ? (
        <>
          <blockquote>«{cleanQuote(dose.quote || dose.value || "")}»</blockquote>
          <div className="sub">
            ОХЛП {dose.section ?? ""} · КР: доза не указана
          </div>
        </>
      ) : (
        l && <div className="sub">ОХЛП: доза не указана · КР: доза не указана</div>
      )}
      {candidate && (
        <>
          <h3>Основание КР</h3>
          <SourceQuotes sources={[{ document: "КР", ref: shortSource(candidate.row.source).replace(/^КР\s*/, ""), section: candidate.evidence ? `УУР/УДД ${candidate.evidence}` : null, quote: candidate.row.quote }]} />
        </>
      )}
    </aside>
  );
}
