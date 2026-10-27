import { Ellipsis } from "../../../../ui/Ellipsis";
/**
 * Patient card frame for the pages outside the card's <Outlet/> (draft, visit workspace): sheet,
 * breadcrumb, the card's profile column (`ProfileAside`) and the main column. Same markup as the boards.
 */
import type { ReactNode } from "react";
import { Link, NavLink } from "react-router";
import { usePatientSummary } from "../../../../api/queries";
import { ErrorBox } from "../../../../ui/states";
import { ProfileAside } from "../parts/ProfileAside";

const TABS = [
  ["", "Сводка"],
  ["safety", "Профиль безопасности"],
  ["diary", "Дневник"],
  ["scales", "Шкалы"],
  ["therapy", "Терапия"],
  ["visits", "Приёмы и случай"],
] as const;

export function CardTabs({ patientId, active }: { patientId: string; active: string }) {
  return (
    <nav className="tabs" aria-label="Разделы карты пациента">
      {TABS.map(([path, label]) => (
        <NavLink
          key={path}
          end
          to={`/d/patients/${patientId}${path ? `/${path}` : ""}`}
          className={() => `tab${path === active ? " on" : ""}`}
        >
          {label}
        </NavLink>
      ))}
    </nav>
  );
}

export function PatientFrame({ patientId, crumb, children }: { patientId: string; crumb?: string; children: ReactNode }) {
  const summary = usePatientSummary(patientId);
  const name = summary.data?.full_name ?? "…";
  return (
    <main className="sheet">
      <div className="sub patient-crumb">
        <Link to="/d/patients" style={{ color: "var(--muted)" }}>
          Пациенты
        </Link>{" "}
        /{" "}
        {crumb ? (
          <>
            <Link to={`/d/patients/${patientId}`} style={{ color: "var(--muted)" }}>
              <Ellipsis>{name}</Ellipsis>
            </Link>{" "}
            / {crumb}
          </>
        ) : (
          <Ellipsis>{name}</Ellipsis>
        )}
      </div>
      {summary.isError ? (
        <ErrorBox error={summary.error} onRetry={() => void summary.refetch()} />
      ) : (
        <div className="pc">
          <ProfileAside patientId={patientId} />
          <div className="pmain">{children}</div>
        </div>
      )}
    </main>
  );
}
