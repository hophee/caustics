/**
 * Board I2-Overview: open safety signals with their delivery track, and the diary strip of the last
 * 14 days for every patient. Appointments and «Ждут вашего решения» have no backend yet (D-113).
 */
import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import type { Alert, PatientSummary } from "../../api/types";
import { ageShort, dayMonth, plural, time } from "../../lib/format";
import { AlertLevelPill } from "../../ui/levels";
import { useScreen } from "../../ui/screen";
import { ErrorBox, Loading, errorText } from "../../ui/states";
import { AlertDialog } from "./AlertDialog";
import { trackLine, trackSteps } from "./alertTrack";
import { NewPatientDialog } from "./NewPatientDialog";
import { SQUARE_TITLE, daysSince, isStale, squares } from "./patientsModel";
import { useAcceptAlert, useOpenAlerts, useOverviewPatients } from "./queries";
import "./Overview.css";

/** Fallback for a patient beyond the first overview page. */
function usePatient(patientId: string, known: PatientSummary | undefined) {
  return useQuery({
    queryKey: ["patient", patientId, "summary"],
    queryFn: () => api.get<PatientSummary>(`/overview/patients/${patientId}`),
    enabled: !known,
  }).data ?? known;
}

function AlertCard({ alert, known, onOpen }: { alert: Alert; known: PatientSummary | undefined; onOpen: () => void }) {
  const patient = usePatient(alert.patient_id, known);
  const accept = useAcceptAlert();
  const accepted = alert.status === "accepted";
  const steps = trackSteps(alert);
  const line = trackLine(alert);
  return (
    <article
      className={`card alert${alert.level === "urgent" ? " urgent" : ""}`}
      onClick={onOpen}
      onKeyDown={(event) => {
        if (event.key === "Enter" && event.target === event.currentTarget) onOpen();
      }}
      tabIndex={0}
      aria-label={alert.reason}
    >
      <div className="top">
        <div>
          <div className="who">
            <AlertLevelPill level={alert.level} scenario={alert.scenario} />
            {patient ? (
              <Link to={`/d/patients/${alert.patient_id}`} className="nm" onClick={(event) => event.stopPropagation()}>
                {patient.full_name}
              </Link>
            ) : (
              <b className="nm">…</b>
            )}
            {patient?.main_icd && <span className="code">{patient.main_icd}</span>}
            {patient && <span className="muted">{ageShort(patient.birth_date)}</span>}
          </div>
          <div style={{ marginTop: 6 }}>{alert.reason}</div>
        </div>
        {accepted ? (
          <button
            className="btn btn-p"
            type="button"
            onClick={(event) => {
              event.stopPropagation();
              onOpen();
            }}
          >
            Записать решение
          </button>
        ) : (
          <button
            className="btn btn-t"
            type="button"
            disabled={accept.isPending}
            onClick={(event) => {
              event.stopPropagation();
              accept.mutate(alert.alert_id);
            }}
          >
            Принять
          </button>
        )}
      </div>
      <div className="trk">
        {steps.map((step) => (
          <div key={step.status} className={`tk${step.miss ? " miss" : ""}`}>
            <span className={`bar${step.miss ? " miss" : step.done ? " on" : ""}`} />
            {step.label}
          </div>
        ))}
      </div>
      <div className="ptxt">
        {line.last}
        {line.next && (
          <>
            {" · "}
            {line.miss ? <span className="late">{line.next}</span> : line.next}
          </>
        )}
      </div>
      {accept.isError && (
        <div role="alert" className="err">
          {errorText(accept.error)}
        </div>
      )}
    </article>
  );
}

function lastEntry(p: PatientSummary, now: Date): string {
  if (!p.last_entry_at) return "нет записей";
  const days = daysSince(p.last_entry_at, now);
  if (days === 0) return "сегодня";
  if (days === 1) return "вчера";
  return `${days} ${plural(days, "день", "дня", "дней")} назад`;
}

function DiaryStrip({ patients }: { patients: PatientSummary[] }) {
  const now = new Date();
  return (
    <div className="card strip">
      {patients.map((p) => (
        <Link className="prow" key={p.patient_id} to={`/d/patients/${p.patient_id}/diary`}>
          <span style={{ fontWeight: 500 }}>{p.full_name}</span>
          <span className="days">
            {squares(p.days).map((sq, index) => (
              <i key={p.days[index]?.day ?? index} className={`sq ${sq}`} title={`${p.days[index] ? dayMonth(p.days[index].day) : ""} · ${SQUARE_TITLE[sq]}`} />
            ))}
          </span>
          <span className={`when${isStale(p, now) ? " stale" : ""}`}>{lastEntry(p, now)}</span>
        </Link>
      ))}
      <div className="legend">
        <span>
          <i className="s" />
          Стабильно
        </span>
        <span>
          <i className="a" />
          Требует внимания
        </span>
        <span>
          <i className="u" />
          Срочно
        </span>
        <span>
          <i className="n" />
          Нет записи
        </span>
      </div>
    </div>
  );
}

export function Overview() {
  useScreen("overview");
  const alerts = useOpenAlerts();
  const patients = useOverviewPatients();
  const [openId, setOpenId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);

  const byId = useMemo(() => new Map((patients.data?.items ?? []).map((p) => [p.patient_id, p])), [patients.data]);
  const open = (alerts.data?.items ?? []).filter((a) => a.status !== "handled");
  const openAlert = open.find((a) => a.alert_id === openId);
  const active = (patients.data?.items ?? []).filter((p) => p.case_status === "active");
  const firstDay = active[0]?.days[0]?.day;

  return (
    <main className="sheet">
      <div className="head">
        <div>
          <h1>Обзор</h1>
        </div>
        <div style={{ display: "flex", gap: 8, flex: "none" }}>
          <button className="btn btn-t" type="button" onClick={() => setCreating(true)}>
            Новый пациент
          </button>
          <Link className="btn btn-t" to="/d/patients">
            Начать приём
          </Link>
        </div>
      </div>

      <div className="cols">
        <div className="col">
          <div className="hd">
            <h2>Сигналы безопасности · {alerts.data?.total ?? "…"}</h2>
            {alerts.dataUpdatedAt > 0 && (
              <span className="muted" style={{ fontSize: 12 }}>
                обновлено {time(new Date(alerts.dataUpdatedAt))}
              </span>
            )}
          </div>
          {alerts.isPending && <Loading />}
          {alerts.isError && <ErrorBox error={alerts.error} onRetry={() => void alerts.refetch()} />}
          {alerts.isSuccess && open.length === 0 && (
            <div className="card list">
              <div className="li">
                <span className="muted">Открытых сигналов нет</span>
              </div>
            </div>
          )}
          {open.map((alert) => (
            <AlertCard key={alert.alert_id} alert={alert} known={byId.get(alert.patient_id)} onOpen={() => setOpenId(alert.alert_id)} />
          ))}
        </div>

        <div className="col">
          <div className="hd">
            <h2>Дневник за 14 дней</h2>
            {firstDay && (
              <span className="muted" style={{ fontSize: 12 }}>
                {dayMonth(firstDay)} — сегодня
              </span>
            )}
          </div>
          {patients.isPending && <Loading />}
          {patients.isError && <ErrorBox error={patients.error} onRetry={() => void patients.refetch()} />}
          {patients.isSuccess && <DiaryStrip patients={active} />}
          {patients.isSuccess && patients.data.total > patients.data.items.length && (
            <Link className="sub" to="/d/patients">
              Все пациенты · {patients.data.total}
            </Link>
          )}
        </div>
      </div>
      {openAlert && <AlertDialog alert={openAlert} onClose={() => setOpenId(null)} />}
      {creating && <NewPatientDialog onClose={() => setCreating(false)} />}
    </main>
  );
}

