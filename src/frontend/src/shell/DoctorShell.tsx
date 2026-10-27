/** Doctor layout (board I2-*): brand, top bar with search and avatar, side navigation, sheet. */
import { useState } from "react";
import { Link, NavLink, Outlet, useLocation, useNavigate } from "react-router";
import { useAuth, useMe } from "../auth/AuthContext";
import { initials } from "../lib/format";
import { CompassMark, MenuIcon, SearchIcon } from "../ui/icons";
import { ScreenRoot } from "../ui/screen";
import { useRecentPatients } from "./recent";

function Nav({ to, end, sub, children }: { to: string; end?: boolean; sub?: boolean; children: React.ReactNode }) {
  return (
    <NavLink to={to} end={end} className={({ isActive }) => `nav${sub ? " sub" : ""}${isActive ? " on" : ""}`}>
      {children}
    </NavLink>
  );
}

export function DoctorShell() {
  const me = useMe();
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const location = useLocation();
  const recent = useRecentPatients();
  const [menuOpen, setMenuOpen] = useState(false);
  const [accountOpen, setAccountOpen] = useState(false);
  const [lastPath, setLastPath] = useState(location.pathname);

  if (lastPath !== location.pathname) {
    setLastPath(location.pathname);
    setMenuOpen(false);
    setAccountOpen(false);
  }

  return (
    <ScreenRoot
      render={(classes) => (
        <div className={`k ${classes}${menuOpen ? " menu-open" : ""}`}>
          <Link className="brand" to="/d">
            <CompassMark />
            <b>Клинический компас</b>
          </Link>
          <div className="tb">
            <form
              className="search"
              role="search"
              onSubmit={(event) => {
                event.preventDefault();
                const q = new FormData(event.currentTarget).get("q")?.toString().trim() ?? "";
                navigate(q ? `/d/patients?q=${encodeURIComponent(q)}` : "/d/patients");
              }}
            >
              <label className="sr" htmlFor="global-search">
                Поиск пациента
              </label>
              <input id="global-search" name="q" placeholder="Пациент" autoComplete="off" />
              <SearchIcon />
            </form>
            <div style={{ position: "relative" }}>
              <button
                className="ava-s"
                type="button"
                title={me.full_name}
                aria-haspopup="menu"
                aria-expanded={accountOpen}
                onClick={() => setAccountOpen((v) => !v)}
                style={{ border: "none", cursor: "pointer" }}
              >
                {initials(me.full_name)}
              </button>
              {accountOpen && (
                <div className="card account-menu" role="menu">
                  <div style={{ fontWeight: 500 }}>{me.full_name}</div>
                  <button className="btn btn-t" type="button" role="menuitem" onClick={signOut}>
                    Выйти
                  </button>
                </div>
              )}
            </div>
            <button
              className="btn btn-t menu"
              type="button"
              aria-label="Меню"
              aria-expanded={menuOpen}
              style={{ width: 36, padding: 0 }}
              onClick={() => setMenuOpen((v) => !v)}
            >
              <MenuIcon />
            </button>
          </div>
          <aside className="side">
            <Nav to="/d" end>
              Обзор
            </Nav>
            <Nav to="/d/patients">Пациенты</Nav>
            <Nav to="/d/kb" end>
              База знаний
            </Nav>
            <Nav to="/d/kb/drugs" sub>
              Препараты и ОХЛП
            </Nav>
            <Nav to="/d/kb/scales" sub>
              Шкалы
            </Nav>
            <Nav to="/d/kb/diagnoses" sub>
              Диагнозы
            </Nav>
            {recent.length > 0 && <div className="grp">Недавние пациенты</div>}
            {recent.map((r) => (
              <Nav key={r.id} to={`/d/patients/${r.id}`} sub>
                {r.name}
              </Nav>
            ))}
          </aside>
          <Outlet />
        </div>
      )}
    />
  );
}
