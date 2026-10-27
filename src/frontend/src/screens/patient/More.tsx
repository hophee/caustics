/** «Ещё» (board P-More): name, notifications, referrals, sign out. Documents are hidden (D-113). */
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Link, useNavigate } from "react-router";
import { api } from "../../api/client";
import type { Notification, PatientReferral } from "../../api/types";
import { useAuth, useMe } from "../../auth/AuthContext";
import { date, dayMonthLong, time } from "../../lib/format";
import { useScreen } from "../../ui/screen";
import { ErrorBox, Q } from "../../ui/states";
import { meKeys } from "./patientApi";
import { EmergencyContacts } from "../../ui/EmergencyContacts";
import "./patient.css";

/** Patient-facing notification kinds: title and where it leads. Other kinds are not shown. */
export const NOTIFICATION_KINDS: Record<string, { title: string; to: string }> = {
  prescription: { title: "Назначение обновлено", to: "/p/meds" },
  dose_reminder: { title: "Напоминание о приёме", to: "/p" },
  referral: { title: "Новое направление", to: "/p/more#referrals" },
};

export function visibleNotifications(items: readonly Notification[], limit = 5): Notification[] {
  return items
    .filter((n) => n.kind in NOTIFICATION_KINDS)
    .sort((a, b) => b.seq - a.seq)
    .slice(0, limit);
}

const KIND_LABELS: Record<string, string> = {
  consultation: "Консультация",
  examination: "Обследование",
  procedure: "Процедура",
  hospitalization: "Госпитализация",
};

export function More() {
  useScreen("p-more");
  const me = useMe();
  const { signOut } = useAuth();
  const navigate = useNavigate();
  const queryClient = useQueryClient();

  const notifications = useQuery({ queryKey: meKeys.notifications, queryFn: () => api.get<Notification[]>("/me/notifications", { limit: 50 }) });
  const referrals = useQuery({ queryKey: meKeys.referrals, queryFn: () => api.get<PatientReferral[]>("/me/referrals") });
  const read = useMutation({
    mutationFn: (id: string) => api.post(`/me/notifications/${id}/read`),
    onSettled: () => queryClient.invalidateQueries({ queryKey: ["notifications"] }),
  });

  const shown = visibleNotifications(notifications.data ?? []);

  return (
    <main className="wrap narrow">
      <h1>Ещё</h1>
      <section className="card me">
        <span className="photo">
          <svg width="52" height="52" viewBox="0 0 64 64" fill="#c9c5bc" aria-hidden="true">
            <circle cx="32" cy="24" r="12" />
            <path d="M8 64c2-14 12-22 24-22s22 8 24 22z" />
          </svg>
        </span>
        <div>
          <h2>{me.full_name}</h2>
        </div>
      </section>

      <EmergencyContacts />

      {(shown.length > 0 || notifications.isError) && (
        <section className="card">
          <h2>Уведомления</h2>
          {notifications.isError && <ErrorBox error={notifications.error} onRetry={() => void notifications.refetch()} />}
          <div className="rows" style={{ marginTop: 6 }}>
            {shown.map((n) => {
              const kind = NOTIFICATION_KINDS[n.kind];
              if (!kind) return null;
              return (
                <Link
                  key={n.notification_id}
                  className="r"
                  to={kind.to}
                  onClick={() => {
                    if (!n.read) read.mutate(n.notification_id);
                  }}
                >
                  <div style={{ display: "flex", gap: 10, alignItems: "baseline" }}>
                    <span className="dot" style={n.read ? { background: "transparent" } : undefined} aria-label={n.read ? undefined : "Новое"} />
                    <div>
                      <div>{kind.title}</div>
                      <div className="sub">
                        {dayMonthLong(n.created_at)}, {time(n.created_at)}
                      </div>
                    </div>
                  </div>
                  <span className="chev" />
                </Link>
              );
            })}
          </div>
        </section>
      )}

      <Q query={referrals}>
        {(list) =>
          list.length > 0 && (
            <section className="card" id="referrals">
              <h2>Направления</h2>
              <div className="rows" style={{ marginTop: 6 }}>
                {list.map((r) => (
                  <div key={r.referral_id} className="r">
                    <div>
                      <div style={r.status === "cancelled" ? { textDecoration: "line-through" } : undefined}>
                        {KIND_LABELS[r.kind] ?? r.kind}: {r.specialist}
                      </div>
                      <div className="sub">
                        {r.status === "cancelled" ? "Отменено" : `Действует до ${date(r.valid_until)}`}
                        {r.category_label ? ` · ${r.category_label}` : ""}
                      </div>
                    </div>
                  </div>
                ))}
              </div>
            </section>
          )
        }
      </Q>

      <section className="card">
        <div className="rows">
          <button
            type="button"
            className="r"
            onClick={() => {
              signOut();
              void navigate("/login", { replace: true });
            }}
          >
            <span>Выйти</span>
            <span className="chev" />
          </button>
        </div>
      </section>
    </main>
  );
}
