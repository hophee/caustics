/** Admin «Пользователи» (D-088): every registered account; the admin gives or takes the doctor role.
 * Demo accounts are not listed. Patients are linked by their doctor's invite code, not here. */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { api } from "../../api/client";
import type { AdminUser, Page, Role } from "../../api/types";
import { useAuth, useMe } from "../../auth/AuthContext";
import { date } from "../../lib/format";
import { CompassMark } from "../../ui/icons";
import { ErrorBox, Loading } from "../../ui/states";
import { errorLines } from "../public/auth";
import "./admin.css";

const ROLE_LABEL: Record<Role, string> = {
  pending: "Без роли",
  patient: "Пациент",
  doctor: "Врач",
  admin: "Администратор",
};
const FILTERS: (Role | "")[] = ["", "pending", "doctor", "patient", "admin"];

function useUsers(q: string, role: Role | "") {
  return useQuery({
    queryKey: ["admin", "users", q, role],
    queryFn: () =>
      api.get<Page<AdminUser>>("/admin/users", { ...(q.trim() ? { q: q.trim() } : {}), ...(role ? { role } : {}), limit: 200 }),
  });
}

function Detail({ u }: { u: AdminUser }) {
  if (u.role === "doctor") return <>{`пациентов: ${u.patients ?? 0}`}</>;
  if (u.role === "patient" && u.doctor_name) return <>{u.doctor_name}</>;
  return null;
}

function Action({ u, onError }: { u: AdminUser; onError: (lines: string[]) => void }) {
  const queryClient = useQueryClient();
  const change = useMutation({
    mutationFn: (kind: "grant" | "revoke") =>
      kind === "grant"
        ? api.post(`/admin/users/${u.user_id}/doctor`, {})
        : api.post(`/admin/users/${u.user_id}/revoke-doctor`, {}),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: ["admin", "users"] }),
    onError: (error: unknown) => onError(errorLines(error)),
  });
  if (u.role === "pending")
    return (
      <button className="btn btn-p" type="button" disabled={change.isPending} onClick={() => change.mutate("grant")}>
        Сделать врачом
      </button>
    );
  if (u.role === "doctor")
    return (
      <button
        className="btn btn-t"
        type="button"
        disabled={change.isPending || (u.patients ?? 0) > 0}
        title={(u.patients ?? 0) > 0 ? "Сначала передайте пациентов другому врачу" : undefined}
        onClick={() => change.mutate("revoke")}
      >
        Снять роль врача
      </button>
    );
  return null;
}

export function AdminUsers() {
  const me = useMe();
  const { signOut } = useAuth();
  const [q, setQ] = useState("");
  const [role, setRole] = useState<Role | "">("");
  const [errors, setErrors] = useState<string[]>([]);
  const users = useUsers(q, role);

  return (
    <div className="k adm">
      <header className="adm-top">
        <span className="mk">
          <CompassMark size={28} />
          <b>Клинический компас</b>
        </span>
        <span className="who">{me.full_name}</span>
        <button className="btn btn-t" type="button" onClick={signOut}>
          Выйти
        </button>
      </header>
      <main className="sheet">
        <h1>Пользователи</h1>
        <div className="adm-filters">
          <input className="inp" type="search" placeholder="Имя или почта" value={q} onChange={(e) => setQ(e.target.value)} />
          <div className="seg" role="tablist">
            {FILTERS.map((f) => (
              <button
                key={f || "all"}
                type="button"
                role="tab"
                aria-selected={role === f}
                className={role === f ? "on" : undefined}
                onClick={() => setRole(f)}
              >
                {f ? ROLE_LABEL[f] : "Все"}
              </button>
            ))}
          </div>
        </div>
        {errors.length > 0 && (
          <div role="alert" className="form-err">
            {errors.map((line) => (
              <div key={line}>{line}</div>
            ))}
          </div>
        )}
        {users.isPending ? (
          <Loading />
        ) : users.isError ? (
          <ErrorBox error={users.error} />
        ) : (
          <table className="adm-table">
            <thead>
              <tr>
                <th>Имя</th>
                <th>Почта</th>
                <th>Роль</th>
                <th />
                <th>С</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {users.data.items.map((u) => (
                <tr key={u.user_id}>
                  <td>{u.name}</td>
                  <td className="mail">{u.email}</td>
                  <td>
                    <span className={`role role-${u.role}`}>{ROLE_LABEL[u.role as Role]}</span>
                  </td>
                  <td className="muted">
                    <Detail u={u} />
                  </td>
                  <td className="muted">{date(u.created_at)}</td>
                  <td className="act">
                    <Action
                      u={u}
                      onError={(lines) => {
                        setErrors(lines);
                      }}
                    />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </main>
    </div>
  );
}
