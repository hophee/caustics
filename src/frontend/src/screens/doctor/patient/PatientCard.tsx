import { Ellipsis } from "../../../ui/Ellipsis";
/** Doctor's patient card (board I2-Summary layout): breadcrumb, profile box, tabs, the tab in <Outlet/>. */
import { useEffect } from "react";
import { Link, NavLink, Outlet } from "react-router";
import { usePatientSummary } from "../../../api/queries";
import { rememberPatient } from "../../../shell/recent";
import { ErrorBox } from "../../../ui/states";
import { usePatientId } from "./parts/data";
import { ProfileAside } from "./parts/ProfileAside";
import "./PatientCard.css";

const TABS: { to: string; label: string; end?: boolean }[] = [
  { to: "", label: "Сводка", end: true },
  { to: "safety", label: "Профиль безопасности" },
  { to: "diary", label: "Дневник" },
  { to: "scales", label: "Шкалы" },
  { to: "therapy", label: "Терапия" },
  { to: "visits", label: "Приёмы и случай" },
];

export function PatientCard() {
  const patientId = usePatientId();
  const summary = usePatientSummary(patientId);
  const name = summary.data?.full_name;

  useEffect(() => {
    if (name) rememberPatient({ id: patientId, name });
  }, [patientId, name]);

  return (
    <main className="sheet">
      <div className="sub patient-crumb">
        <Link to="/d/patients" style={{ color: "var(--muted)" }}>
          Пациенты
        </Link>{" "}
        / <Ellipsis>{name ?? "…"}</Ellipsis>
      </div>
      {summary.isError ? (
        <ErrorBox error={summary.error} onRetry={() => void summary.refetch()} />
      ) : (
        <div className="pc">
          <ProfileAside patientId={patientId} />
          <div className="pmain">
            <nav className="tabs" aria-label="Разделы карты пациента">
              {TABS.map((tab) => (
                <NavLink
                  key={tab.to}
                  to={tab.to ? `/d/patients/${patientId}/${tab.to}` : `/d/patients/${patientId}`}
                  end={tab.end}
                  className={({ isActive }) => `tab${isActive ? " on" : ""}`}
                >
                  {tab.label}
                </NavLink>
              ))}
            </nav>
            <Outlet />
          </div>
        </div>
      )}
    </main>
  );
}
