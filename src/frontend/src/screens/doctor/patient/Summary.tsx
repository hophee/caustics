import { Ellipsis } from "../../../ui/Ellipsis";
/** «Сводка» tab (board I2-Summary): what is open now, the course line, scales and recent diary entries. */
import { useState } from "react";
import { Link } from "react-router";
import type { Alert, DoctorDiaryEntry } from "../../../api/types";
import { useCurrentTherapy, usePatientSummary } from "../../../api/queries";
import { dayMonth, time, when } from "../../../lib/format";
import { AlertLevelPill } from "../../../ui/levels";
import { EntryTriage } from "./parts/EntryTriage";
import "./Summary.css";
import { useScreen } from "../../../ui/screen";
import { ErrorBox, Loading } from "../../../ui/states";
import { AlertDialog } from "../AlertDialog";
import { diaryLane, scaleLane, signalLane, therapyLane } from "./parts/course";
import {
  useCasePeriod,
  useDiary,
  useFreshness,
  useOpenAlerts,
  usePatientId,
  usePrescriptions,
  useResults,
} from "./parts/data";
import { metricsLine } from "./parts/diary";
import { type Period, eventTicks } from "./parts/period";
import { scaleRows, score } from "./parts/scales";
import { alertTitle, alertTrack, patientAlerts, therapyItem, therapyLine, therapyTitle } from "./parts/view";

export function Summary() {
  useScreen("summary");
  const patientId = usePatientId();
  const period = useCasePeriod(patientId);
  return (
    <>
      <NowCells patientId={patientId} />
      {period ? <CourseLine patientId={patientId} period={period} /> : <Loading />}
      <div className="two">
        <ScalesCard patientId={patientId} />
        {period && <RecentEntries patientId={patientId} period={period} />}
      </div>
    </>
  );
}

function NowCells({ patientId }: { patientId: string }) {
  const alerts = useOpenAlerts();
  const summary = usePatientSummary(patientId).data;
  const freshness = useFreshness(patientId).data;
  const therapy = useCurrentTherapy(patientId).data;
  const [open, setOpen] = useState<Alert | null>(null);
  const mine = patientAlerts(alerts.data?.items ?? [], patientId);
  const top = mine[0];
  const items = therapy?.items.map((i) => therapyItem(i)) ?? [];

  return (
    <section className="card now">
      <div className="cell">
        <span className="lbl">{mine.length > 1 ? `Открытые сигналы · ${mine.length}` : "Открытый сигнал"}</span>
        {alerts.isPending ? (
          <span className="muted">…</span>
        ) : top ? (
          <>
            <div>
              <AlertLevelPill level={top.level} scenario={top.scenario} /> {alertTitle(top)}
            </div>
            <span className="sub">{alertTrack(top)}</span>
            <button className="btn btn-p" type="button" style={{ alignSelf: "flex-start", marginTop: 4 }} onClick={() => setOpen(top)}>
              Записать решение
            </button>
          </>
        ) : (
          <div style={{ fontWeight: 500 }}>нет</div>
        )}
      </div>
      <div className="cell">
        <span className="lbl">Дневник</span>
        <div style={{ fontWeight: 500 }}>{summary?.last_entry_at ? when(summary.last_entry_at) : "Нет записей"}</div>
        {freshness && (
          <span className="sub">
            пропусков за {freshness.window_days} дн.: {freshness.missed_days}
          </span>
        )}
      </div>
      <div className="cell">
        <span className="lbl">Терапия</span>
        {therapy === undefined ? (
          <span className="muted">…</span>
        ) : items.length === 0 ? (
          <div style={{ fontWeight: 500 }}>без препаратов</div>
        ) : (
          items.map((item) => (
            <div key={item.key}>
              <div style={{ fontWeight: 500 }}>{therapyTitle(item)}</div>
              <span className="sub">{therapyLine(item)}</span>
            </div>
          ))
        )}
      </div>
      {open && <AlertDialog alert={open} onClose={() => setOpen(null)} />}
    </section>
  );
}

function Grid({ period, children, style }: { period: Period; children: React.ReactNode; style?: React.CSSProperties }) {
  return (
    <div className="g" style={{ gridTemplateColumns: `repeat(${period.days.length},minmax(0,1fr))`, ...style }}>
      {children}
    </div>
  );
}

function CourseLine({ patientId, period }: { patientId: string; period: Period }) {
  const diary = useDiary(patientId, period.start);
  const results = useResults(patientId);
  const prescriptions = usePrescriptions(patientId);
  const alerts = useOpenAlerts();

  const squares = diaryLane(period, diary.data ?? []);
  const points = scaleLane(period, results.data?.items ?? []);
  const segments = therapyLane(period, prescriptions.data?.items ?? []);
  const signals = signalLane(period, patientAlerts(alerts.data?.items ?? [], patientId));
  const eventDays = [
    ...(diary.data ?? []).map((entry) => entry.day),
    ...points.map((point) => point.day),
    ...signals.map((signal) => period.days[signal.col - 1]!),
    ...segments.filter((segment) => segment.kind === "rx")
      .flatMap((segment) => period.days.slice(segment.col - 1, segment.col - 1 + segment.span)),
  ];
  const labelled = points.length > 2 ? [points[0], points[points.length - 1]] : points;
  const error = diary.error ?? results.error ?? prescriptions.error;

  return (
    <section className="card">
      <div className="ch">
        <h2>
          Линия курса · {dayMonth(period.start)} — {dayMonth(period.end)}
        </h2>
        <Link className="btn btn-t" to={`/d/patients/${patientId}/visit`}>
          Начать приём
        </Link>
      </div>
      {error && <ErrorBox error={error} />}
      <div className="course-scroll" role="region" aria-label="Линия курса" tabIndex={0}>
      <div className="course" style={{ minWidth: Math.max(440, 128 + period.days.length * 28) }}>
        <div className="lane">
          <span className="nm2">Дневник</span>
          <Grid period={period}>
            {squares.map((sq, i) => (
              <i key={period.days[i]} className={`sq ${sq}`} title={dayMonth(period.days[i] ?? "")} />
            ))}
          </Grid>
        </div>
        <div className="lane">
          <span className="nm2">Шкалы</span>
          <Grid period={period}>
            {points.map((p) => (
              <span key={p.day} className={`pt${p.critical ? " crit" : ""}`} style={{ gridColumn: p.col + 1, gridRow: 1 }} title={`${dayMonth(p.day)} · ${p.label}`} />
            ))}
          </Grid>
          <div className="scale-labels">
            {labelled.map((p) => p && (
              <span key={p.day} className="lab">
                <Ellipsis>{`${dayMonth(p.day)} · ${p.label}`}</Ellipsis>
              </span>
            ))}
          </div>
        </div>
        <div className="lane">
          <span className="nm2">Терапия</span>
          <Grid period={period}>
            {segments.map((s) =>
              s.kind === "rx" ? (
                <span key={`rx${s.col}${s.label}`} className="rx" style={{ gridColumn: `${s.col} / span ${s.span}`, gridRow: 1 }} title={s.label} />
              ) : (
                <span key={`obs${s.col}`} className="obs" style={{ gridColumn: `${s.col} / span ${s.span}`, gridRow: 1 }} />
              ),
            )}
            {segments.map((s) =>
              s.kind === "obs" && s.span >= 8 ? (
                <span
                  key={`lab${s.col}`}
                  className="lab muted"
                  style={{ gridColumn: `${s.col + 1} / span ${s.span - 1}`, gridRow: 1, justifySelf: "start", background: "#fff", padding: "0 6px" }}
                >
                  {s.label}
                </span>
              ) : null,
            )}
          </Grid>
        </div>
        <div className="lane">
          <span className="nm2">Сигналы</span>
          <Grid period={period}>
            {signals.map((s) => (
              <span
                key={s.id}
                className={`lv ${s.cls}`}
                style={{ gridColumn: `${Math.max(1, s.col - 3)} / span ${Math.min(4, s.col)}`, justifySelf: "end" }}
              >
                {s.label}
              </span>
            ))}
          </Grid>
        </div>
        <div className="ax">
          <span></span>
          <Grid period={period}>
            {eventTicks(period, eventDays).map((t) => (
              <span key={t.day} style={{ gridColumn: t.col, gridRow: 1, justifySelf: t.col === period.days.length ? "end" : "start", whiteSpace: "nowrap" }}>
                {dayMonth(t.day)}
              </span>
            ))}
          </Grid>
        </div>
        <div className="leg"><strong>Шкалы</strong>
          <span>
            <i className="pt" style={{ display: "inline-block" }}></i>шкала
          </span>
          <span>
            <i className="pt crit" style={{ display: "inline-block" }}></i>критический ответ
          </span>
        </div>
        <div className="leg"><strong>Терапия</strong>
          <span>
            <i className="obs" style={{ display: "inline-block", width: 18 }}></i>без препаратов
          </span>
          <span>
            <i className="rx" style={{ display: "inline-block", width: 18 }}></i>курс
          </span>
        </div>
        <div className="leg"><strong>Дневник</strong>
          <span>
            <i className="sq s" style={{ width: 24, height: 14 }}></i>стабильно
          </span>
          <span>
            <i className="sq a" style={{ width: 24, height: 14 }}></i>требует внимания
          </span>
          <span>
            <i className="sq u" style={{ width: 24, height: 14 }}></i>срочно
          </span>
          <span>
            <i className="sq n" style={{ width: 24, height: 14 }}></i>нет записи
          </span>
        </div>
      </div>
      </div>
    </section>
  );
}

function Arrow({ delta }: { delta: number }) {
  if (delta === 0) return <span className="chg flat">0</span>;
  const down = delta < 0;
  return (
    <span className={`chg ${down ? "down" : "up"}`} aria-label={`${down ? "снизился" : "вырос"} на ${score(Math.abs(delta))}`}>
      <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.6" aria-hidden="true">
        <path d={down ? "M12 4v16M5 13l7 7 7-7" : "M12 20V4M5 11l7-7 7 7"} />
      </svg>
      {score(Math.abs(delta))}
    </span>
  );
}

function ScalesCard({ patientId }: { patientId: string }) {
  const results = useResults(patientId);
  const rows = scaleRows(results.data?.items ?? []);
  return (
    <section className="card">
      <div className="ch">
        <h2>Шкалы</h2>
        <Link to={`/d/patients/${patientId}/scales`} className="sub">
          Назначить анкету
        </Link>
      </div>
      {results.isPending ? (
        <div className="pad">
          <Loading />
        </div>
      ) : results.isError ? (
        <div className="pad">
          <ErrorBox error={results.error} />
        </div>
      ) : rows.length === 0 ? (
        <div className="empty">Результатов нет</div>
      ) : (
        <div className="scroll">
          <table>
            <thead>
              <tr>
                <th>Шкала</th>
                <th className="num">Первое</th>
                <th className="num">Последнее</th>
                <th className="num">Δ</th>
                <th>Уровень</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.code}>
                  <td style={{ fontWeight: 500 }}>{r.code}</td>
                  <td className="num">
                    {score(r.first.value)}
                    <span className="d2">{dayMonth(r.first.day)}</span>
                  </td>
                  {r.last ? (
                    <td className="num">
                      {score(r.last.value)}
                      <span className="d2">{dayMonth(r.last.day)}</span>
                    </td>
                  ) : (
                    <td className="num muted">—</td>
                  )}
                  <td className={`num${r.delta === null ? " muted" : ""}`}>{r.delta === null ? "—" : <Arrow delta={r.delta} />}</td>
                  <td className="wrap">{r.band ?? "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}

function RecentEntries({ patientId, period }: { patientId: string; period: Period }) {
  const diary = useDiary(patientId, period.start);
  const latest: DoctorDiaryEntry[] = [...(diary.data ?? [])]
    .sort((a, b) => b.day.localeCompare(a.day) || b.saved_at.localeCompare(a.saved_at))
    .slice(0, 2);
  return (
    <section className="card">
      <div className="ch">
        <h2>Последние записи дневника</h2>
        <Link to={`/d/patients/${patientId}/diary`} className="sub">
          Весь дневник
        </Link>
      </div>
      {diary.isPending ? (
        <div className="pad">
          <Loading />
        </div>
      ) : latest.length === 0 ? (
        <div className="empty">Нет записей</div>
      ) : (
        latest.map((e) => (
          <div key={e.entry_id} className="entry">
            <div style={{ display: "flex", justifyContent: "space-between", gap: 12 }}>
              <span style={{ fontWeight: 500 }}>
                {dayMonth(e.day)}, {time(e.saved_at)}
              </span>
              <EntryTriage triage={e.triage} />
            </div>
            {e.text ? <div>«{e.text}»</div> : <div className="muted">без текста</div>}
            <span className="sub">
              {metricsLine(e)}
              {e.notes.length > 0 && (
                <>
                  {" · "}
                  <Link to={`/d/patients/${patientId}/diary`}>заметка</Link>
                </>
              )}
            </span>
          </div>
        ))
      )}
    </section>
  );
}
