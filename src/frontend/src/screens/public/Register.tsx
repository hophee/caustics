/** Self-registration (D-088): the new account has no role until a doctor's code or an admin gives one. */
import { useState, type ChangeEvent } from "react";
import { Link, Navigate, useNavigate } from "react-router";
import { HOME } from "../../app/routes";
import { useAuth } from "../../auth/AuthContext";
import { errorLines, registerProblems } from "./auth";
import { AuthMark, FormErrors } from "./Login";
import "./public.css";

export function Register() {
  const { me, loading, register } = useAuth();
  const navigate = useNavigate();
  const [form, setForm] = useState({ name: "", email: "", password: "", repeat: "" });
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  if (!loading && me) return <Navigate to={HOME[me.role]} replace />;

  const field = (name: keyof typeof form) => ({
    value: form[name],
    onChange: (e: ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [name]: e.target.value })),
  });

  return (
    <div className="k au scr-s-login">
      <AuthMark />
      <form
        className="box"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          const problems = registerProblems(form);
          setErrors(problems);
          if (problems.length) return;
          setBusy(true);
          register(form.email.trim(), form.password, form.name.trim())
            .then((user) => void navigate(HOME[user.role], { replace: true }))
            .catch((error: unknown) => setErrors(errorLines(error)))
            .finally(() => setBusy(false));
        }}
      >
        <h1>Регистрация</h1>
        <div className="fld">
          <label htmlFor="n">Имя и фамилия</label>
          <input id="n" className="inp" autoComplete="name" {...field("name")} />
        </div>
        <div className="fld">
          <label htmlFor="e">Электронная почта</label>
          <input id="e" className="inp" type="email" autoComplete="email" {...field("email")} />
        </div>
        <div className="fld">
          <label htmlFor="p1">Пароль</label>
          <input id="p1" className="inp" type="password" autoComplete="new-password" {...field("password")} />
        </div>
        <div className="fld">
          <label htmlFor="p2">Пароль ещё раз</label>
          <input id="p2" className="inp" type="password" autoComplete="new-password" {...field("repeat")} />
        </div>
        <FormErrors lines={errors} />
        <button className="btn btn-p" type="submit" disabled={busy}>
          Зарегистрироваться
        </button>
        <Link className="alt" to="/login">
          Уже есть учётная запись
        </Link>
      </form>
    </div>
  );
}
