/**
 * The doctor's draft (board I2-Draft, spec §6): current therapy with stop/keep, new items with their
 * regimens, a live re-check on every change painting each card and dose box with its severity, all
 * warnings with sources (A05), dose sources from ОХЛП and КР shown separately (A04), and confirmation
 * with exactly the justifications the backend asks for (A06 is open: nothing more is enforced here).
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { api, ApiError } from "../../../api/client";
import { keys, usePatientSummary, useProfile } from "../../../api/queries";
import type { CheckReport, Profile, Severity, Warning } from "../../../api/types";
import { dayMonth } from "../../../lib/format";
import { Lv } from "../../../ui/levels";
import { useScreen } from "../../../ui/screen";
import { ErrorBox, Loading, errorText } from "../../../ui/states";
import { Dialog } from "../../../ui/Dialog";
import { useDraftState } from "./therapy/store";
import { useCheck, useCurrent, useLabel, usePrescriptions, type PrescriptionVersion } from "./therapy/api";
import { itemTitle, joinCurrent, labelNames, type CurrentView } from "./therapy/current";
import { useDebounced } from "./therapy/hooks";
import {
  EMPTY_DRAFT,
  buildRegimen,
  cardCheck,
  cleanQuote,
  confirmProblems,
  countWarnings,
  draftForCheck,
  draftForConfirm,
  isEmptyDraft,
  isoToday,
  justificationsPayload,
  newEntry,
  rebase,
  unplacedWarnings,
  type CardCheck,
  type ConfirmProblems,
  type DraftEntry,
  type DraftState,
  type RegimenErrors,
} from "./therapy/model";
import { CardTabs, PatientFrame } from "./therapy/PatientFrame";
import { SourceQuotes, WarningLine } from "./therapy/parts";
import { Picker, type PickedLabel } from "./therapy/Picker";
import { doseText, courseText, normalizeRegimen } from "./therapy/regimen";
import { DateInput, TimeInput } from "../../../ui/DateInput";
import "./Draft.css";
import { useEncounter } from "./visit/store";

export function Draft() {
  useScreen("draft");
  const { patientId = "" } = useParams();
  return (
    <PatientFrame patientId={patientId}>
      <CardTabs patientId={patientId} active="therapy" />
      <DraftBody patientId={patientId} />
    </PatientFrame>
  );
}

const RISK = { low: "низкий", high: "высокий", unknown: "Неизвестно" } as const;

function riskLabel(risk: Profile["suicide_risk"] | undefined): string {
  return risk ? RISK[risk] : "Неизвестно";
}

const sevClass = (s: Severity | "unchecked" | null) => `sev-${s ?? "none"}`;

function DraftBody({ patientId }: { patientId: string }) {
  const client = useQueryClient();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [encounter, setEncounter] = useEncounter(patientId);
  const fromVisit = params.get("visit") === "1";
  const summary = usePatientSummary(patientId);
  const profile = useProfile(patientId);
  const current = useCurrent(patientId);
  const versions = usePrescriptions(patientId, 50);
  const [stored, setDraft] = useDraftState(patientId);
  const [required, setRequired] = useState<Map<string, string>>(new Map());
  const [texts, setTexts] = useState<Record<string, string>>({});
  const [problems, setProblems] = useState<ConfirmProblems["other"]>([]);
  const [notice, setNotice] = useState<string | null>(null);
  const [showErrors, setShowErrors] = useState(false);
  const [confirmationDialog, setConfirmationDialog] = useState<"unchanged" | "justify" | null>(null);
  const today = isoToday();

  const refs = useMemo(() => (current.data?.items ?? []).map((i) => i.ref), [current.data]);
  const state = useMemo(() => (current.data ? rebase(stored, refs) : stored), [stored, refs, current.data]);
  const { draft: payload, unchecked } = useMemo(() => draftForCheck(state, today), [state, today]);
  const debounced = useDebounced(payload, 300);
  const check = useCheck(patientId, debounced, current.data?.base_token);
  const report = check.data;
  const checking = check.isFetching || JSON.stringify(debounced) !== JSON.stringify(payload);

  const prescriptions = useMemo(() => versions.data?.items ?? [], [versions.data]);
  const currentViews = useMemo(() => joinCurrent(current.data?.items ?? [], prescriptions), [current.data, prescriptions]);
  const names = useMemo(() => {
    const map = labelNames(prescriptions);
    for (const item of state.items) if (!map.has(item.labelId)) map.set(item.labelId, item.title.split(" (")[0] ?? item.title);
    return map;
  }, [prescriptions, state.items]);
  const loose = unplacedWarnings(report, [...state.items.map((i) => i.key), ...currentViews.map((c) => c.key)]);

  const update = (next: (prev: DraftState) => DraftState) => {
    setNotice(null);
    setDraft((prev) => next(current.data ? rebase(prev, refs) : prev));
  };

  const confirm = useMutation({
    mutationFn: (body: unknown) => api.post<PrescriptionVersion>(`/patients/${patientId}/basket/confirm`, body),
    onSuccess: (prescription) => {
      setDraft(EMPTY_DRAFT);
      void client.invalidateQueries({ queryKey: keys.patient(patientId) });
      if (fromVisit) {
        setEncounter((v) => ({...v, therapyConfirmed: true, prescriptionId: prescription.prescription_id}));
        navigate(`/d/patients/${patientId}/visit?step=summary`);
      } else navigate(`/d/patients/${patientId}/therapy`);
    },
    onError: (error) => {
      if (error instanceof ApiError && error.status === 422) {
        const found = confirmProblems(error.details);
        setRequired(found.justifications);
        setProblems(found.other);
        setNotice(found.justifications.size > 0 ? null : error.message);
        if (found.justifications.size > 0) setConfirmationDialog("justify");
      } else if (error instanceof ApiError && error.status === 409) {
        setNotice(error.message);
        setRequired(new Map());
        setConfirmationDialog(null);
        void client.invalidateQueries({ queryKey: keys.patient(patientId) });
      } else {
        setNotice(errorText(error));
      }
    },
  });

  const sendConfirmation = () => {
    if (!current.data) return;
    if (isEmptyDraft(state)) {
      setConfirmationDialog("unchanged");
      return;
    }
    const built = draftForConfirm(state);
    if (!built.draft) {
      setShowErrors(true);
      setConfirmationDialog(null);
      setNotice("Заполните дозировку, время приёма и длительность курса в карточках препаратов.");
      return;
    }
    setProblems([]);
    setNotice(null);
    confirm.mutate({
      draft: built.draft,
      base_token: current.data.base_token,
      justifications: justificationsPayload(required.keys(), texts),
    });
  };
  const submit = () => {
    if (!isEmptyDraft(state) && required.size > 0) setConfirmationDialog("justify");
    else sendConfirmation();
  };

  if (current.isPending || summary.isPending) return <Loading />;
  if (current.isError) return <ErrorBox error={current.error} onRetry={() => void current.refetch()} />;

  const counts = countWarnings(report);
  const icd = summary.data?.main_icd ?? "";
  const reviewed = profile.data?.current_medications_reviewed_at;
  const picking = params.get("pick") === "1";
  const setPicking = (open: boolean) =>
    setParams(
      (prev) => {
        const next = new URLSearchParams(prev);
        if (open) next.set("pick", "1");
        else next.delete("pick");
        return next;
      },
      { replace: true },
    );
  const justification = (id: string) => (
    <JustificationBox
      key={id}
      id={id}
      message={required.get(id) ?? ""}
      warning={report?.warnings.find((w) => w.id === id)}
      value={texts[id] ?? ""}
      onChange={(text) => setTexts((prev) => ({ ...prev, [id]: text }))}
    />
  );

  return (
    <>
      <div className="draftbar">
        <b style={{ fontWeight: 500 }}>Черновик изменений</b>
        {!isEmptyDraft(state) && (
          <button
            type="button"
            className="linkbtn"
            onClick={() => {
              setDraft(EMPTY_DRAFT);
              setRequired(new Map());
              setProblems([]);
              setShowErrors(false);
            }}
          >
            Отменить черновик
          </button>
        )}
      </div>
      {notice && (
        <div className="notice" role="alert">
          {notice}
        </div>
      )}
      {problems.length > 0 && (
        <div className="notice" role="alert">
          {problems.map((p, i) => (
            <div key={`${p.code}-${i}`}>{p.message}</div>
          ))}
        </div>
      )}
      {loose.length > 0 && (
        <section className="card">
          <div className="pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {loose.map((w) => (
              <WarningLine key={w.id} warning={w} names={names} />
            ))}
          </div>
        </section>
      )}
      <div className="cols">
        <div className="col">
          <section className="card">
            <div className="ch">
              <h2>Сейчас принимает</h2>
              <span className="sub">{reviewed ? `подтверждено ${dayMonth(reviewed)}` : "список не подтверждён"}</span>
            </div>
            {currentViews.length === 0 ? (
              <div className="pad">Ничего не принимает</div>
            ) : (
              <div className="pad" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                {currentViews.map((view) => (
                  <CurrentCard
                    key={view.ref}
                    view={view}
                    stopped={state.stopped.includes(view.ref)}
                    card={cardCheck(report, view.key)}
                    names={names}
                    onEdit={view.labelId && !state.stopped.includes(view.ref) ? () => update((prev) => {
                      if (!view.labelId) return prev;
                      const regimen = normalizeRegimen(view.regimen);
                      const entry = newEntry(prev, {labelId: view.labelId, title: itemTitle(view), origin: {kind: "search"}, context: "Изменение дозировки текущего препарата"}, today);
                      return {...prev, stopped: [...new Set([...prev.stopped, view.ref])], items: [...prev.items, {...entry, replaces: view.ref,
                        regimen: {start: today, duration: String(regimen?.durationDays ?? ""), intakes: regimen?.intakes.map((i) => ({at: i.at, dose: `${i.amount} ${i.unit}`})) ?? [{at: "", dose: ""}]}}]};
                    }) : undefined}
                    onToggle={() =>
                      update((prev) => ({
                        ...prev,
                        stopped: prev.stopped.includes(view.ref) ? prev.stopped.filter((r) => r !== view.ref) : [...prev.stopped, view.ref],
                      }))
                    }
                  >
                  </CurrentCard>
                ))}
              </div>
            )}
          </section>
          {state.items.map((item) => (
            <NewItemCard
              key={item.key}
              item={item}
              card={cardCheck(report, item.key, unchecked)}
              errors={showErrors ? buildRegimen(item.regimen).errors : null}
              names={names}
              onChange={(regimen) =>
                update((prev) => ({ ...prev, items: prev.items.map((i) => (i.key === item.key ? { ...i, regimen } : i)) }))
              }
              onRemove={() => update((prev) => ({ ...prev, items: prev.items.filter((i) => i.key !== item.key), stopped: item.replaces ? prev.stopped.filter((ref) => ref !== item.replaces) : prev.stopped }))}
            >
            </NewItemCard>
          ))}
          <button type="button" className="add" onClick={() => setPicking(true)}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
              <path d="M12 5v14M5 12h14" />
            </svg>
            Добавить препарат
          </button>
        </div>
        <div className="col">
          <section className="card" aria-busy={checking}>
            <div className="ch">
              <h2>Проверка схемы</h2>
              {checking && <span className="sub">проверка…</span>}
            </div>
            <div className="pad" style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              {check.isError ? (
                <ErrorBox error={check.error} onRetry={() => void check.refetch()} />
              ) : (
                <>
                  <Count label="Противопоказания" value={counts.contraindicated} red />
                  <Count label="С осторожностью" value={counts.caution} />
                  <Count label="Нет данных" value={counts.no_data} />
                  <Count label="Взаимодействия" value={counts.interactions} />
                </>
              )}
            </div>
          </section>
          <Basis report={report} items={state.items} names={names} />
        </div>
      </div>
      <div className="foot">
        <Link className="btn btn-t" to={fromVisit ? `/d/patients/${patientId}/visit?step=decision` : `/d/patients/${patientId}/therapy`}>
          {fromVisit ? "Вернуться к приёму" : "Вернуться к карточке"}
        </Link>
        <button type="button" className="btn btn-p" disabled={confirm.isPending} onClick={submit}>
          {confirm.isPending ? "Подтверждение…" : "Подтвердить назначение"}
        </button>
      </div>
      {confirmationDialog && <Dialog name="prescription-confirm" labelledBy="prescription-confirm-title"
        onClose={() => {if (!confirm.isPending) setConfirmationDialog(null);}}>
        <div className="mh"><h2 id="prescription-confirm-title">{confirmationDialog === "unchanged" ? "В назначении нет изменений" : "Назначение требует обоснования"}</h2></div>
        <div className="confirmation-body">
          {confirmationDialog === "unchanged" ? <>
            <p>Показана текущая терапия. Новый препарат, изменение дозировки или отмена ещё не добавлены, поэтому новое назначение не создаётся.</p>
            {(report?.warnings.length ?? 0) > 0 && <><h3>Предупреждения по текущей терапии</h3>
              {report?.warnings.map((w) => <WarningLine key={w.id} warning={w} names={names} />)}</>}
            {checking && <p role="status">Проверка текущей схемы…</p>}
            {check.isError && <ErrorBox error={check.error} onRetry={() => void check.refetch()} />}
            <p>Чтобы изменить лечение, вернитесь к аптечке. Решение о продолжении лечения можно указать в приёме.</p>
          </> : <>
            <p>Выявлены противопоказания. Ознакомьтесь с причинами и источниками и укажите обоснование решения для каждого предупреждения. Назначение пока не сохранено.</p>
            {[...required.keys()].map((id) => {
              const warning = report?.warnings.find((w) => w.id === id);
              return <section key={id} className="confirmation-warning">
                {warning && <WarningLine warning={warning} names={names} />}
                {justification(id)}
              </section>;
            })}
            {notice && <p role="alert">{notice}</p>}
            {problems.map((p, i) => <p role="alert" key={`${p.code}-${i}`}>{p.message}</p>)}
          </>}
        </div>
        <div className="mf"><button type="button" className="btn btn-t" disabled={confirm.isPending} onClick={() => setConfirmationDialog(null)}>Вернуться к аптечке</button>
          {confirmationDialog === "justify" && <button type="button" className="btn btn-p"
            disabled={confirm.isPending || [...required.keys()].some((id) => !(texts[id] ?? "").trim())}
            onClick={sendConfirmation}>{confirm.isPending ? "Подтверждение…" : "Подтвердить с обоснованием"}</button>}
        </div>
      </Dialog>}
      {picking && (
        <Picker
          selectionKey={fromVisit ? `${encounter.id ?? "draft"}:${params.get("mode")}` : undefined}
          expanded={fromVisit && encounter.kind === "repeat" && params.get("mode") === "prescription"}
          patientId={patientId}
          icd={icd}
          patientName={summary.data?.full_name ?? ""}
          suicideRisk={riskLabel(profile.data?.suicide_risk)}
          currentNames={currentViews.filter((v) => !state.stopped.includes(v.ref)).map((v) => itemTitle(v))}
          base={payload}
          baseToken={current.data.base_token}
          draftCount={state.items.length}
          female={summary.data?.sex === "female"}
          onClose={() => setPicking(false)}
          onAdd={(picked: PickedLabel) => {
            update((prev) => ({ ...prev, items: [...prev.items, newEntry(prev, picked, today)] }));
            setPicking(false);
          }}
        />
      )}
    </>
  );
}

function Count({ label, value, red }: { label: string; value: number; red?: boolean }) {
  return (
    <div className="cnt">
      <span>{label}</span>
      <b style={{ fontWeight: 500, color: red && value > 0 ? "#a4161a" : undefined }}>{value}</b>
    </div>
  );
}

function CurrentCard({
  view,
  stopped,
  card,
  names,
  onToggle,
  onEdit,
  children,
}: {
  view: CurrentView;
  stopped: boolean;
  card: CardCheck;
  names: Map<string, string>;
  onToggle: () => void;
  onEdit?: () => void;
  children?: React.ReactNode;
}) {
  const label = useLabel(view.item ? null : view.labelId);
  const title = itemTitle(view, label.data?.display_name);
  const regimen = normalizeRegimen(view.regimen);
  return (
    <div className={`item ${stopped ? "sev-none stopped" : sevClass(card.item)}`}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ fontWeight: 500 }}>
            {stopped && <Lv cls="lv-pln">Отменить</Lv>} <span className={stopped ? "struck" : undefined}>{title}</span>
          </div>
          <div className="sub">
            {[doseText(regimen), courseText(regimen), view.patientReported ? "со слов пациента" : null].filter(Boolean).join(" · ")}
          </div>
        </div>
        {onEdit && <button type="button" className="linkbtn" onClick={onEdit}>Изменить дозировку</button>}
        <button type="button" className="linkbtn" onClick={onToggle}>
          {stopped ? "Вернуть" : "Отменить"}
        </button>
      </div>
      {!stopped && card.warnings.map((w) => <WarningLine key={w.id} warning={w} names={names} />)}
      {!stopped && children}
    </div>
  );
}

function fieldError(text: string | undefined) {
  return text ? <div className="err">{text}</div> : null;
}

function NewItemCard({
  item,
  card,
  errors,
  names,
  onChange,
  onRemove,
  children,
}: {
  item: DraftEntry;
  card: CardCheck;
  errors: RegimenErrors | null;
  names: Map<string, string>;
  onChange: (regimen: DraftEntry["regimen"]) => void;
  onRemove: () => void;
  children?: React.ReactNode;
}) {
  const r = item.regimen;
  const setIntake = (i: number, patch: Partial<{ at: string; dose: string }>) =>
    onChange({ ...r, intakes: r.intakes.map((x, j) => (j === i ? { ...x, ...patch } : x)) });
  const id = (name: string, i = 0) => `${item.key}-${name}-${i}`;
  return (
    <div className={`item ${sevClass(card.item)}`}>
      <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
        <div>
          <div style={{ fontWeight: 500 }}>Добавить · {item.title}</div>
          <div className="sub">{item.context}</div>
        </div>
        <button type="button" className="linkbtn" onClick={onRemove}>
          Убрать
        </button>
      </div>
      {r.intakes.map((intake, i) => (
        <div className="fields" key={i}>
          <div>
            <label htmlFor={id("dose", i)}>Доза</label>
            <input
              id={id("dose", i)}
              className={`inp dose ${sevClass(card.dose)}`}
              value={intake.dose}
              inputMode="decimal"
              aria-invalid={Boolean(errors?.intakes[i]?.dose)}
              onChange={(event) => setIntake(i, { dose: event.target.value })}
            />
            {fieldError(errors?.intakes[i]?.dose)}
          </div>
          <div>
            <label htmlFor={id("at", i)}>Время</label>
            <TimeInput
              id={id("at", i)}
              value={intake.at}
              aria-invalid={Boolean(errors?.intakes[i]?.at)}
              onChange={(value) => setIntake(i, { at: value })}
            />
            {fieldError(errors?.intakes[i]?.at)}
          </div>
          {i === 0 ? (
            <>
              <div>
                <label htmlFor={id("start")}>Начало</label>
                <DateInput
                  id={id("start")}
                  value={r.start}
                  aria-invalid={Boolean(errors?.start)}
                  onChange={(value) => onChange({ ...r, start: value })}
                />
                {fieldError(errors?.start)}
              </div>
              <div>
                <label htmlFor={id("duration")}>Длительность</label>
                <input
                  id={id("duration")}
                  className="inp"
                  inputMode="numeric"
                  value={r.duration}
                  aria-invalid={Boolean(errors?.duration)}
                  onChange={(event) => onChange({ ...r, duration: event.target.value })}
                />
                {fieldError(errors?.duration)}
              </div>
            </>
          ) : (
            <div className="intake-x">
              <button type="button" className="linkbtn" onClick={() => onChange({ ...r, intakes: r.intakes.filter((_, j) => j !== i) })}>
                Убрать приём
              </button>
            </div>
          )}
        </div>
      ))}
      <button
        type="button"
        className="linkbtn"
        style={{ alignSelf: "flex-start" }}
        onClick={() => onChange({ ...r, intakes: [...r.intakes, { at: "", dose: "" }] })}
      >
        Добавить приём
      </button>
      {card.warnings.map((w) => (
        <WarningLine key={w.id} warning={w} names={names} />
      ))}
      {children}
    </div>
  );
}

function JustificationBox({
  id,
  message,
  warning,
  value,
  onChange,
}: {
  id: string;
  message: string;
  warning: Warning | undefined;
  value: string;
  onChange: (text: string) => void;
}) {
  const field = `just-${id}`;
  return (
    <div className="just">
      <label htmlFor={field} style={{ fontWeight: 500, color: "#a4161a" }}>
        {warning?.severity === "contraindicated" || !warning ? "Обоснование назначения при противопоказании" : "Обоснование"}
      </label>
      <textarea id={field} className="inp" value={value} onChange={(event) => onChange(event.target.value)} />
      {message && <div className="err">{message}</div>}
    </div>
  );
}

/** Sources behind the warnings, plus the dose text of each new item: ОХЛП and КР separately (A04). */
function Basis({ report, items, names }: { report: CheckReport | undefined; items: DraftEntry[]; names: Map<string, string> }) {
  const sources = useMemo(() => {
    const seen = new Set<string>();
    return (report?.warnings ?? [])
      .flatMap((w) => w.sources)
      .filter((s) => {
        const key = `${s.document}|${s.ref}|${s.section ?? ""}|${s.quote ?? ""}`;
        if (seen.has(key) || !s.quote) return false;
        seen.add(key);
        return true;
      });
  }, [report]);
  if (sources.length === 0 && items.length === 0) return null;
  return (
    <section className="card">
      <div className="ch">
        <h2>Основания</h2>
      </div>
      {items.map((item) => (
        <DoseBasis key={item.key} item={item} />
      ))}
      {sources.map((s, i) => (
        <div className="src" key={`${s.ref}-${s.section ?? ""}-${i}`}>
          <SourceQuotes sources={[s]} names={names} />
        </div>
      ))}
    </section>
  );
}

function DoseBasis({ item }: { item: DraftEntry }) {
  const label = useLabel(item.labelId);
  const dose = label.data?.dose.start_text;
  const text = cleanQuote(dose?.quote || dose?.value || "");
  return (
    <div className="src">
      <span className="sub">
        <Link to={`/d/kb/drugs/${item.labelId}`} style={{ color: "inherit" }}>
          ОХЛП {label.data?.trade_name ?? ""}
        </Link>
        {dose?.section ? ` · ${dose.section}` : ""}
      </span>
      {label.isPending ? <Loading /> : text ? <blockquote>«{text}»</blockquote> : <span className="sub">ОХЛП: доза не указана</span>}
      <span className="sub">КР: доза не указана</span>
    </div>
  );
}
