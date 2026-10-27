/** The doctor’s patient table, with search, status filters and invitations. */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../../api/client";
import { ReactionNotice, type Reaction } from "./Pharmacovigilance";
import { Link, useSearchParams } from "react-router";
import type { PatientSummary } from "../../api/types";
import { date, time } from "../../lib/format";
import { AlertLevelPill } from "../../ui/levels";
import { useScreen } from "../../ui/screen";
import { ErrorBox, Loading } from "../../ui/states";
import { InviteDialog, NewPatientDialog } from "./NewPatientDialog";
import { STATUS_META, activityAgo, filterPatients, statusCounts, type StatusKey } from "./patientsModel";
import { MAX_LIMIT, useOverviewPatients } from "./queries";
import "./Patients.css";

function SearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#5f5d58" strokeWidth="1.8" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-4-4" />
    </svg>
  );
}

function PatientRow({ p, now, onInvite, reactions }: { p: PatientSummary; now: Date; onInvite: () => void; reactions: Reaction[] }) {
  return (
    <tr className={p.open_alerts > 0 && p.top_alert_level === "urgent" ? "urgent" : undefined}>
      <th scope="row">
        <Link className="patient-name" to={`/d/patients/${p.patient_id}`}>{p.full_name}</Link>
        <ReactionNotice events={reactions} patient={p} />
        {!p.has_account && (
          <div className="sub">
            Аккаунт не активирован
            <button className="btn btn-t inv" type="button" onClick={onInvite}>Новый код</button>
          </div>
        )}
      </th>
      <td>{p.last_visit_at ? <time dateTime={p.last_visit_at}>{date(p.last_visit_at)}</time> : <span className="muted">Нет приёмов</span>}</td>
      <td>
        {p.last_activity_at ? (
          <>
            <time dateTime={p.last_activity_at}>{date(p.last_activity_at)}, {time(p.last_activity_at)}</time>{" "}
            <span className="muted">({activityAgo(p.last_activity_at, now)})</span>
          </>
        ) : <span className="muted">Нет активности</span>}
      </td>
      <td>
        {p.open_alerts > 0 ? (
          <div className="patient-alerts">
            <span>{p.open_alerts}</span>
            {p.top_alert_level && <AlertLevelPill level={p.top_alert_level} />}
          </div>
        ) : <span className="muted">Нет алёртов</span>}
      </td>
    </tr>
  );
}

export function Patients() {
  useScreen("patients");
  const [params, setParams] = useSearchParams();
  const q = params.get("q") ?? "";
  const page = Math.max(0, Number(params.get("page") ?? 0) || 0);
  const [status, setStatus] = useState<StatusKey | null>(null);
  const [creating, setCreating] = useState(false);
  const [inviteFor, setInviteFor] = useState<PatientSummary | null>(null);
  const query = useOverviewPatients(page * MAX_LIMIT, MAX_LIMIT);
  const reactions = useQuery({ queryKey: ["reaction-todo"], queryFn: () => api.get<Reaction[]>("/adverse-events/todo"), refetchInterval: 10000 });
  const now = new Date();

  const update = (next: Record<string, string | null>) => {
    const merged = new URLSearchParams(params);
    for (const [key, value] of Object.entries(next)) {
      if (value) merged.set(key, value);
      else merged.delete(key);
    }
    setParams(merged, { replace: true });
  };

  const all = query.data?.items ?? [];
  const matching = filterPatients(all, q, null, now);
  const shown = filterPatients(matching, "", status, now);
  const counts = statusCounts(matching, now);
  const total = query.data?.total ?? 0;
  const pages = Math.ceil(total / MAX_LIMIT);

  return (
    <main className="sheet">
      <div className="head">
        <h1>Пациенты</h1>
        <button className="btn btn-p" type="button" onClick={() => setCreating(true)}>
          Новый пациент
        </button>
      </div>
      <div className="bar2">
        <div className="flt">
          <button type="button" className={`f${status === null ? " on" : ""}`} onClick={() => setStatus(null)}>
            Все · {matching.length}
          </button>
          {counts.map(({ key, count }) => (
            <button key={key} type="button" className={`f${status === key ? " on" : ""}`} onClick={() => setStatus(status === key ? null : key)}>
              {STATUS_META[key].label} · {count}
            </button>
          ))}
        </div>
        <label className="loc">
          <span className="sr">Найти пациента</span>
          <input
            className="inp"
            type="search"
            placeholder="Имя или код МКБ"
            value={q}
            onChange={(event) => update({ q: event.target.value || null })}
          />
          <SearchIcon />
        </label>
      </div>

      {query.isPending && <Loading />}
      {reactions.isError && <ErrorBox error={reactions.error} onRetry={() => void reactions.refetch()} />}
      {query.isError && <ErrorBox error={query.error} onRetry={() => void query.refetch()} />}
      {query.isSuccess && shown.length === 0 && <div className="muted">Никого не найдено</div>}
      {shown.length > 0 && (
        <div className="patients-table-wrap" role="region" aria-label="Список пациентов" tabIndex={0}>
          <table className="patients-table">
            <caption className="sr">Пациенты</caption>
            <thead>
              <tr>
                <th scope="col">ФИО</th>
                <th scope="col">Дата последнего приёма</th>
                <th scope="col">Дата последней активности</th>
                <th scope="col">Алёрты</th>
              </tr>
            </thead>
            <tbody>
              {shown.map((p) => (
                <PatientRow key={p.patient_id} p={p} now={now} onInvite={() => setInviteFor(p)} reactions={(reactions.data ?? []).filter((r) => r.patient_id === p.patient_id)} />
              ))}
            </tbody>
          </table>
        </div>
      )}
      {pages > 1 && (
        <nav className="pager" aria-label="Страницы">
          <button className="btn btn-t" type="button" disabled={page === 0} onClick={() => update({ page: page > 1 ? String(page - 1) : null })}>
            Назад
          </button>
          <span className="sub">
            {page + 1} из {pages}
          </span>
          <button className="btn btn-t" type="button" disabled={page + 1 >= pages} onClick={() => update({ page: String(page + 1) })}>
            Далее
          </button>
        </nav>
      )}
      {creating && <NewPatientDialog onClose={() => setCreating(false)} />}
      {inviteFor && <InviteDialog patient={inviteFor} onClose={() => setInviteFor(null)} />}
    </main>
  );
}
