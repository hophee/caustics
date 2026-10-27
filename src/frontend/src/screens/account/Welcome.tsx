/** A registered account without a role (D-088): enter the doctor's invite code to become their patient.
 * Doctors get their role from an admin; this screen then sends them on (the role is re-read on refresh). */
import { useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate, useSearchParams } from "react-router";
import { HOME } from "../../app/routes";
import { useAuth, useMe } from "../../auth/AuthContext";
import { errorLines, normalizeCode } from "../public/auth";
import { AuthMark, FormErrors } from "../public/Login";
import "../public/public.css";

export function Welcome() {
  const me = useMe();
  const { link, signOut } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();
  const [params] = useSearchParams();
  const [code, setCode] = useState(params.get("code") ?? "");
  const [busy, setBusy] = useState(false);
  const [errors, setErrors] = useState<string[]>([]);

  return (
    <div className="k au scr-s-login">
      <AuthMark />
      <form
        className="box"
        noValidate
        onSubmit={(e) => {
          e.preventDefault();
          if (!normalizeCode(code)) {
            setErrors(["Введите код приглашения"]);
            return;
          }
          setBusy(true);
          setErrors([]);
          link(normalizeCode(code))
            .then((user) => void navigate(HOME[user.role], { replace: true }))
            .catch((error: unknown) => setErrors(errorLines(error)))
            .finally(() => setBusy(false));
        }}
      >
        <h1>{me.full_name}</h1>
        <div className="fld">
          <label htmlFor="c">Код от врача</label>
          <input
            id="c"
            className="inp code"
            maxLength={16}
            autoComplete="one-time-code"
            autoCapitalize="characters"
            spellCheck={false}
            value={code}
            onChange={(e) => setCode(e.target.value)}
          />
        </div>
        <FormErrors lines={errors} />
        <button className="btn btn-p" type="submit" disabled={busy}>
          Подключиться к врачу
        </button>
        <button className="btn" type="button" onClick={() => void queryClient.invalidateQueries({ queryKey: ["auth", "me"] })}>
          Обновить
        </button>
        <button className="alt linkish" type="button" onClick={signOut}>
          Выйти
        </button>
      </form>
    </div>
  );
}
