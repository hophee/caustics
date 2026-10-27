/** First sign-in of a patient with the doctor's invite code (board S-Activate). */
import { useState, type ChangeEvent } from "react";
import { Link, Navigate, useNavigate, useSearchParams } from "react-router";
import { HOME } from "../../app/routes";
import { useAuth } from "../../auth/AuthContext";
import { activateProblems, errorLines, normalizeCode } from "./auth";
import { AuthMark, FormErrors } from "./Login";

export function Activate() {
  const { me, loading, activate } = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const [form, setForm] = useState({ code: params.get("code") ?? "", email: "", password: "", repeat: "" });
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  if (!loading && me) {
    // a signed-in account without a role links the code on its waiting screen (D-088)
    const code = normalizeCode(form.code);
    const next = me.role === "pending" && code ? `${HOME.pending}?code=${encodeURIComponent(code)}` : HOME[me.role];
    return <Navigate to={next} replace />;
  }

  const field = (name: keyof typeof form) => ({
    value: form[name],
    onChange: (e: ChangeEvent<HTMLInputElement>) => setForm((f) => ({ ...f, [name]: e.target.value })),
  });

  return (
    <div className="k au scr-s-activate">
      <AuthMark />
      <form
        className="box"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          const problems = activateProblems(form);
          setErrors(problems);
          if (problems.length) return;
          setBusy(true);
          activate(normalizeCode(form.code), form.email.trim(), form.password)
            .then((user) => void navigate(HOME[user.role], { replace: true }))
            .catch((error: unknown) => setErrors(errorLines(error)))
            .finally(() => setBusy(false));
        }}
      >
        <h1>Код приглашения</h1>
        <div className="fld">
          <label htmlFor="c">Код от врача</label>
          <input id="c" className="inp code" maxLength={16} autoComplete="one-time-code" autoCapitalize="characters" spellCheck={false} {...field("code")} />
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
          Создать учётную запись
        </button>
        <Link className="alt" to="/login">
          Уже есть учётная запись
        </Link>
      </form>
    </div>
  );
}
