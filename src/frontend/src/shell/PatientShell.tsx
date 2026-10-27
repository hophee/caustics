/** Patient layout (board I2-Today, C layout): top bar on desktop, bottom tabs on phones. */
import { Link, NavLink, Outlet } from "react-router";
import { ScreenRoot } from "../ui/screen";

const SECTIONS = [
  { to: "/p", label: "Сегодня", end: true },
  { to: "/p/diary", label: "Дневник" },
  { to: "/p/visits", label: "Приёмы" },
  { to: "/p/forms", label: "Анкеты" },
  { to: "/p/meds", label: "Лекарства" },
  { to: "/p/more", label: "Ещё" },
];

export function PatientShell() {
  return (
    <ScreenRoot
      render={(classes) => (
        <div className={`p ${classes}`}>
          <header className="bar">
            <Link className="mark" to="/p">
              <svg width="22" height="22" viewBox="0 0 32 32" fill="none" stroke="#fff" strokeWidth="2" role="img" aria-label="Клинический компас">
                <circle cx="16" cy="16" r="12" />
                <path d="M19.5 12.5L14 14l-1.5 5.5L18 18z" fill="#fff" />
              </svg>
            </Link>
            <nav className="top" aria-label="Разделы">
              {SECTIONS.map((s) => (
                <NavLink key={s.to} to={s.to} end={s.end} className={({ isActive }) => (isActive ? "on" : "")}>
                  {s.label}
                </NavLink>
              ))}
            </nav>
            <Link to="/p/help" className="btn btn-s help-link">
              Нужна помощь?
            </Link>
          </header>
          <Outlet />
          <nav className="tabs" aria-label="Разделы">
            {SECTIONS.map((s) => (
              <NavLink key={s.to} to={s.to} end={s.end} className={({ isActive }) => `tab${isActive ? " on" : ""}`}>
                {s.label}
              </NavLink>
            ))}
          </nav>
        </div>
      )}
    />
  );
}
