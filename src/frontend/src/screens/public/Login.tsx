/** Sign-in (board S-Login). After signing in: back to `location.state.from`, else the role's home. */
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { Link, Navigate, useLocation, useNavigate } from "react-router";
import { api } from "../../api/client";
import type { AuthConfig } from "../../api/types";
import { HOME } from "../../app/routes";
import { useAuth } from "../../auth/AuthContext";
import { CompassMark } from "../../ui/icons";
import { afterSignIn, errorLines } from "./auth";
import "./public.css";

/** Brand mark with the name, linking to the project page. */
export function AuthMark() {
  return (
    <Link className="mk" to="/about">
      <CompassMark size={36} />
      <b>Клинический компас</b>
    </Link>
  );
}

export function FormErrors({ lines }: { lines: string[] }) {
  if (!lines.length) return null;
  return (
    <div role="alert" className="form-err">
      {lines.map((line) => (
        <div key={line}>{line}</div>
      ))}
    </div>
  );
}

/** Demo mode only: the same synthetic patient and their treating doctor in one click. */
function DemoButtons({ onError }: { onError: (lines: string[]) => void }) {
  const { demo } = useAuth();
  const navigate = useNavigate();
  const [busy, setBusy] = useState(false);
  const config = useQuery({ queryKey: ["auth", "config"], queryFn: () => api.get<AuthConfig>("/auth/config") });
  if (!config.data?.demo_mode) return null;
  const start = (kind: "patient" | "doctor") => {
    setBusy(true);
    demo(kind)
      .then((user) => void navigate(HOME[user.role], { replace: true }))
      .catch((error: unknown) => onError(errorLines(error)))
      .finally(() => setBusy(false));
  };
  return (
    <div className="demo">
      <button className="btn btn-t" type="button" disabled={busy} onClick={() => start("patient")}>
        Попробовать как пациент
      </button>
      <button className="btn btn-t" type="button" disabled={busy} onClick={() => start("doctor")}>
        Попробовать как врач
      </button>
    </div>
  );
}

export function Login() {
  const { me, loading, signIn } = useAuth();
  const location = useLocation();
  const navigate = useNavigate();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  if (!loading && me) return <Navigate to={afterSignIn(location.state, me.role, HOME)} replace />;

  return (
    <div className="k au scr-s-login">
      <AuthMark />
      <form
        className="box"
        onSubmit={(e) => {
          e.preventDefault();
          setBusy(true);
          setErrors([]);
          signIn(email.trim(), password)
            .then((user) => void navigate(afterSignIn(location.state, user.role, HOME), { replace: true }))
            .catch((error: unknown) => setErrors(errorLines(error)))
            .finally(() => setBusy(false));
        }}
      >
        <h1>Вход</h1>
        <div className="fld">
          <label htmlFor="e">Электронная почта</label>
          <input id="e" className="inp" type="email" required value={email} autoComplete="username" onChange={(e) => setEmail(e.target.value)} />
        </div>
        <div className="fld">
          <label htmlFor="pw">Пароль</label>
          <input id="pw" className="inp" type="password" required value={password} autoComplete="current-password" onChange={(e) => setPassword(e.target.value)} />
        </div>
        <FormErrors lines={errors} />
        <button className="btn btn-p" type="submit" disabled={busy}>
          Войти
        </button>
        <Link className="alt" to="/register">
          Зарегистрироваться
        </Link>
        <Link className="alt" to="/activate">
          У меня есть код приглашения
        </Link>
      </form>
      <DemoButtons onError={setErrors} />
    </div>
  );
}
