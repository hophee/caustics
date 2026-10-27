/** «Сегодня» (board I2-Today): mood picker starting the diary, the diary week, today's doses, due questionnaires. */
import { PatientDynamics } from "./Dynamics";
import { Link, useNavigate } from "react-router";
import { useMe } from "../../auth/AuthContext";
import { dayMonthLong, longDay, weekdayShort } from "../../lib/format";
import { PillIcon } from "../../ui/icons";
import { useScreen } from "../../ui/screen";
import { ErrorBox } from "../../ui/states";
import { MOODS, localDay } from "./diaryFlow";
import { DoseMarker } from "./DoseMarker";
import { mg } from "./doses";
import { MoodFace } from "./MoodFace";
import { assignmentLine } from "./questionnaireFlow";
import { useAssignments, useMyDiary, useMyDoses, usePrescription, useScales } from "./patientApi";
import "./patient.css";

export function greeting(now: Date): string {
  const h = now.getHours();
  if (h < 5) return "Доброй ночи";
  if (h < 12) return "Доброе утро";
  if (h < 18) return "Добрый день";
  return "Добрый вечер";
}

/** The last seven days, oldest first, ending today. */
export function lastWeek(now: Date): { day: string; label: string; today: boolean }[] {
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(now);
    d.setDate(now.getDate() - 6 + i);
    return { day: localDay(d), label: i === 6 ? "сегодня" : weekdayShort(d), today: i === 6 };
  });
}

export function Today() {
  useScreen("today");
  const me = useMe();
  const navigate = useNavigate();
  const now = new Date();
  const today = localDay(now);
  const week = lastWeek(now);
  const firstDay = week[0]?.day ?? today;

  const diary = useMyDiary(firstDay, today);
  const doses = useMyDoses(today, today);
  const prescription = usePrescription();
  const assignments = useAssignments();
  const scales = useScales();

  const filled = new Set((diary.data ?? []).map((e) => e.day));
  const firstName = me.first_name || me.full_name;
  const due = assignments.data ?? [];
  const dueDoses = doses.data ?? [];

  return (
    <main className="wrap">
      <div>
        <div className="muted" style={{ fontSize: 15 }}>
          {longDay(now)}
        </div>
        <h1>
          {greeting(now)}, {firstName}
        </h1>
      </div>
      <div className="grid">
        <div className="col">
          <section className="card mood-checkin">
            <h2>Как вы сегодня?</h2>
            <div className="moods">
              {MOODS.map((m) => (
                <button key={m.value} className="mood" type="button" onClick={() => void navigate(`/p/diary?new=1&mood=${m.value}`)}>
                  <MoodFace mouth={m.mouth} />
                  {m.label}
                </button>
              ))}
            </div>
          </section>
          <section className="card">
            <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 12 }}>
              <h2>Неделя в дневнике</h2>
              <Link to="/p/diary" style={{ fontSize: 14 }}>
                Все записи
              </Link>
            </div>
            {diary.isError ? (
              <ErrorBox error={diary.error} onRetry={() => void diary.refetch()} />
            ) : (
              <div className="week" aria-busy={diary.isPending}>
                {week.map((d) => (
                  <span key={d.day} className={`dy${d.today ? " now" : ""}`}>
                    <i className={diary.isPending ? "pending" : filled.has(d.day) ? "" : "no"} aria-label={filled.has(d.day) ? "есть запись" : "нет записи"} />
                    {d.label}
                  </span>
                ))}
              </div>
            )}
          </section>
        </div>

        <div className="col">
          {dueDoses.map((dose) => {
            const line = prescription.data?.find((l) => l.trade_name === dose.name);
            return (
              <section key={`${dose.course_id}|${dose.scheduled_at}`} className="card dose-card">
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
                  <div>
                    <div style={{ fontWeight: 600 }}>
                      {dose.name}, {mg(dose.amount_mg)}
                    </div>
                    <div className="sub">
                      в {dose.at}
                      {line ? ` · курс до ${dayMonthLong(line.end)}` : ""}
                    </div>
                  </div>
                  <span className="icon">
                    <PillIcon />
                  </span>
                </div>
                <DoseMarker dose={dose} />
              </section>
            );
          })}
          {doses.isError && <ErrorBox error={doses.error} onRetry={() => void doses.refetch()} />}

          {due.map((a) => (
            <section key={a.assignment_id} className="card" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: 12 }}>
              <div>
                <div style={{ fontWeight: 600 }}>Анкета от врача</div>
                <div className="sub" style={{ marginTop: 2 }}>
                  {assignmentLine(
                    a,
                    scales.data?.find((s) => s.code === a.scale_code),
                  )}
                </div>
              </div>
              <Link className="btn btn-s" to={`/p/forms/${a.assignment_id}`} style={{ flex: "none" }}>
                Пройти
              </Link>
            </section>
          ))}
          {assignments.isError && <ErrorBox error={assignments.error} onRetry={() => void assignments.refetch()} />}
        </div>
      </div>
      <PatientDynamics />
    </main>
  );
}
