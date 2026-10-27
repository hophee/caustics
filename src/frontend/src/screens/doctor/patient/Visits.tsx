/** «Приёмы и случай» tab (board I2-Visits): visits, diagnoses, the case, psychiatrist referrals. */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { Link } from "react-router";
import { api, newSubmissionId } from "../../../api/client";
import { useActiveCase, useDiagnoses } from "../../../api/queries";
import type { CaseOut, Diagnosis, DoctorReferrals, Visit } from "../../../api/types";
import { date, dayMonth, time } from "../../../lib/format";
import { useScreen } from "../../../ui/screen";
import { ErrorBox, Loading, errorText } from "../../../ui/states";
import { useDiagnosisName, usePatientId, useReferrals, useVisits } from "./parts/data";
import { addDays, localDay } from "./parts/period";
import { normalizeIcd } from "./parts/safety";
import { CATEGORY, KIND, URGENCY, visitFields } from "./parts/visits";
import { DateInput } from "../../../ui/DateInput";
import "./Visits.css";

export function Visits() {
  useScreen("visits");
  const patientId = usePatientId();
  const activeCase = useActiveCase(patientId);
  if (activeCase.isPending) return <Loading />;
  if (activeCase.isError) return <ErrorBox error={activeCase.error} onRetry={() => void activeCase.refetch()} />;
  const c = activeCase.data;
  return (
    <div className="cols">
      <div className="col">
        <VisitsCard patientId={patientId} activeCase={c} />
        <ReferralsCard patientId={patientId} />
      </div>
      <div className="col">
        {c && <DiagnosesCard patientId={patientId} caseId={c.case_id} />}
        <CaseCard patientId={patientId} activeCase={c} />
      </div>
    </div>
  );
}

function VisitsCard({ patientId, activeCase }: { patientId: string; activeCase: CaseOut | null }) {
  const visits = useVisits(patientId, activeCase?.case_id);
  return (
    <section className="card">
      <div className="ch">
        <h2>Приёмы</h2>
        <Link className="btn btn-p" to={`/d/patients/${patientId}/visit`}>
          Начать приём
        </Link>
      </div>
      {!activeCase ? (
        <div className="empty">Нет активного случая</div>
      ) : visits.isPending ? (
        <div className="pad">
          <Loading />
        </div>
      ) : visits.isError ? (
        <div className="pad">
          <ErrorBox error={visits.error} />
        </div>
      ) : visits.data.length === 0 ? (
        <div className="empty">Приёмов нет</div>
      ) : (
        visits.data.map((v: Visit) => (
          <div key={v.visit_id} className="vis">
            <div>
              <div style={{ fontWeight: 500 }}>{dayMonth(v.held_at)}</div>
              <div className="sub">{time(v.held_at)}</div>
            </div>
            <div>
              <div style={{ fontWeight: 500 }}>{v.kind === "first" ? "Первичный" : "Повторный"}</div>
              {visitFields(v.content).length > 0 && (
                <dl>
                  {visitFields(v.content).map((f) => (
                    <VisitField key={f.label} label={f.label} value={f.value} />
                  ))}
                </dl>
              )}
            </div>
            <span />
          </div>
        ))
      )}
    </section>
  );
}

function VisitField({ label, value }: { label: string; value: string }) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

const STATUS: Record<string, { label: string; cls: string }> = {
  established: { label: "установлен", cls: "lv-grn" },
  provisional: { label: "предварительный", cls: "lv-amb" },
};

function DiagnosisRow({ d }: { d: Diagnosis }) {
  const name = useDiagnosisName(d.icd_code).data;
  const status = STATUS[d.status] ?? { label: d.status, cls: "lv-pln" };
  return (
    <div className="dx">
      <div>
        <div style={{ fontWeight: 500 }}>
          {d.icd_code}
          {d.is_main ? " · основной" : ""}
        </div>
        {name && <div className="sub">{name}</div>}
      </div>
      <span className={`lv ${status.cls}`}>{status.label}</span>
    </div>
  );
}

function DiagnosesCard({ patientId, caseId }: { patientId: string; caseId: string }) {
  const diagnoses = useDiagnoses(patientId, caseId);
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [code, setCode] = useState("");
  const [main, setMain] = useState(true);
  const [provisional, setProvisional] = useState(false);
  const normalized = normalizeIcd(code);
  const add = useMutation({
    mutationFn: () => api.post<Diagnosis>(`/cases/${caseId}/diagnoses`, { icd_code: normalized, is_main: main, provisional }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["patient", patientId] });
      setAdding(false);
      setCode("");
    },
  });
  return (
    <section className="card">
      <div className="ch">
        <h2>Диагнозы</h2>
        {!adding && (
          <button type="button" className="linkbtn sub" onClick={() => setAdding(true)}>
            Добавить
          </button>
        )}
      </div>
      {adding && (
        <form
          className="pad"
          style={{ display: "flex", flexDirection: "column", gap: 10, borderBottom: "1px solid var(--rule)" }}
          onSubmit={(e) => {
            e.preventDefault();
            if (normalized) add.mutate();
          }}
        >
          <div className="fld">
            <label htmlFor="dx-code">Код МКБ-10</label>
            <input id="dx-code" className="inp" value={code} onChange={(e) => setCode(e.target.value)} autoFocus aria-invalid={Boolean(code && !normalized)} />
          </div>
          <label className="sm">
            <input type="checkbox" checked={main} onChange={(e) => setMain(e.target.checked)} /> Основной
          </label>
          <label className="sm">
            <input type="checkbox" checked={provisional} onChange={(e) => setProvisional(e.target.checked)} /> Предварительный
          </label>
          {add.isError && <span className="form-err">{errorText(add.error)}</span>}
          <div style={{ display: "flex", gap: 8, justifyContent: "flex-end" }}>
            <button type="button" className="btn btn-t" onClick={() => setAdding(false)}>
              Отменить
            </button>
            <button type="submit" className="btn btn-p" disabled={!normalized || add.isPending}>
              Добавить
            </button>
          </div>
        </form>
      )}
      {diagnoses.isPending ? (
        <div className="pad">
          <Loading />
        </div>
      ) : diagnoses.isError ? (
        <div className="pad">
          <ErrorBox error={diagnoses.error} />
        </div>
      ) : diagnoses.data.length === 0 ? (
        <div className="empty">Диагноз не установлен</div>
      ) : (
        diagnoses.data.map((d) => <DiagnosisRow key={d.diagnosis_id} d={d} />)
      )}
    </section>
  );
}

function CaseCard({ patientId, activeCase }: { patientId: string; activeCase: CaseOut | null }) {
  const queryClient = useQueryClient();
  const [confirming, setConfirming] = useState(false);
  const refresh = () => queryClient.invalidateQueries({ queryKey: ["patient", patientId] });
  const open = useMutation({ mutationFn: () => api.post<CaseOut>(`/patients/${patientId}/cases`), onSuccess: refresh });
  const close = useMutation({
    mutationFn: () => api.post<CaseOut>(`/cases/${activeCase?.case_id}/close`),
    onSuccess: async () => {
      setConfirming(false);
      await refresh();
      await queryClient.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  const error = open.error ?? close.error;
  return (
    <section className="card">
      <div className="ch">
        <h2>Случай</h2>
        {activeCase ? <span className="lv lv-grn">активен</span> : <span className="lv lv-pln">нет активного</span>}
      </div>
      <div className="pad" style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        {activeCase ? (
          <>
            <div>
              <span className="muted">Открыт</span> {date(activeCase.opened_at)}
            </div>
            {confirming ? (
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
                <button type="button" className="btn btn-d" onClick={() => close.mutate()} disabled={close.isPending}>
                  Закрыть случай
                </button>
                <button type="button" className="btn btn-t" onClick={() => setConfirming(false)}>
                  Отменить
                </button>
              </div>
            ) : (
              <button type="button" className="btn btn-d" style={{ alignSelf: "flex-start" }} onClick={() => setConfirming(true)}>
                Закрыть случай
              </button>
            )}
          </>
        ) : (
          <button type="button" className="btn btn-p" style={{ alignSelf: "flex-start" }} onClick={() => open.mutate()} disabled={open.isPending}>
            Открыть случай
          </button>
        )}
        {error && <span className="form-err">{errorText(error)}</span>}
      </div>
    </section>
  );
}

type Rec = DoctorReferrals["recommendations"][number];

function ReferralsCard({ patientId }: { patientId: string }) {
  const referrals = useReferrals(patientId);
  const [mode, setMode] = useState<null | "recommend" | { recommendation: Rec | null }>(null);
  return (
    <section className="card">
      <div className="ch">
        <h2>Направления к психиатру</h2>
        {mode === null && (
          <span style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
            <button type="button" className="linkbtn sub" onClick={() => setMode("recommend")}>
              Рекомендация
            </button>
            <button type="button" className="linkbtn sub" onClick={() => setMode({ recommendation: null })}>
              Выписать
            </button>
          </span>
        )}
      </div>
      {mode === "recommend" && <RecommendForm patientId={patientId} onDone={() => setMode(null)} />}
      {mode !== null && mode !== "recommend" && <ReferralForm patientId={patientId} recommendation={mode.recommendation} onDone={() => setMode(null)} />}
      {referrals.isPending ? (
        <div className="pad">
          <Loading />
        </div>
      ) : referrals.isError ? (
        <div className="pad">
          <ErrorBox error={referrals.error} />
        </div>
      ) : referrals.data.recommendations.length + referrals.data.referrals.length === 0 ? (
        <div className="empty">Направлений нет</div>
      ) : (
        <>
          {referrals.data.recommendations.map((r) => (
            <RecommendationRow
              key={r.recommendation_id}
              rec={r}
              patientId={patientId}
              issued={referrals.data.referrals.some((ref) => ref.recommendation_id === r.recommendation_id && ref.status === "issued")}
              onIssue={() => setMode({ recommendation: r })}
            />
          ))}
          {referrals.data.referrals.map((r) => (
            <ReferralRow key={r.referral_id} referral={r} patientId={patientId} />
          ))}
        </>
      )}
    </section>
  );
}

function ReasonForm({ label, submitText, danger, onSubmit, onCancel, pending, error }: {
  label: string;
  submitText: string;
  danger?: boolean;
  onSubmit: (reason: string) => void;
  onCancel: () => void;
  pending: boolean;
  error: unknown;
}) {
  const [reason, setReason] = useState("");
  const ok = reason.trim().length >= 3;
  return (
    <form
      style={{ display: "flex", flexDirection: "column", gap: 8, marginTop: 8 }}
      onSubmit={(e) => {
        e.preventDefault();
        if (ok) onSubmit(reason.trim());
      }}
    >
      <input className="inp" aria-label={label} placeholder={label} value={reason} onChange={(e) => setReason(e.target.value)} autoFocus />
      {Boolean(error) && <span className="form-err">{errorText(error)}</span>}
      <div style={{ display: "flex", gap: 8 }}>
        <button type="submit" className={`btn ${danger ? "btn-d" : "btn-p"}`} disabled={!ok || pending}>
          {submitText}
        </button>
        <button type="button" className="btn btn-t" onClick={onCancel}>
          Отменить
        </button>
      </div>
    </form>
  );
}

const REC_STATUS: Record<string, { label: string; cls: string }> = {
  open: { label: "ждёт решения", cls: "lv-amb" },
  accepted: { label: "принята", cls: "lv-grn" },
  declined: { label: "отклонена", cls: "lv-pln" },
};

function RecommendationRow({ rec, patientId, issued, onIssue }: { rec: Rec; patientId: string; issued: boolean; onIssue: () => void }) {
  const queryClient = useQueryClient();
  const [deciding, setDeciding] = useState<null | boolean>(null);
  const decide = useMutation({
    mutationFn: (reason: string) => api.post(`/referral-recommendations/${rec.recommendation_id}/decision`, { accept: deciding, reason }),
    onSuccess: async () => {
      setDeciding(null);
      await queryClient.invalidateQueries({ queryKey: ["patient", patientId, "referrals"] });
    },
  });
  const status = REC_STATUS[rec.status] ?? { label: rec.status, cls: "lv-pln" };
  return (
    <div className="ref">
      <div>
        <div style={{ fontWeight: 500 }}>
          Рекомендация · {rec.category_label || CATEGORY[rec.category]}
        </div>
        <div>{rec.reason}</div>
        <div className="sub">
          {[date(rec.created_at), rec.source !== "doctor" ? rec.source : null].filter(Boolean).join(" · ")}
          {rec.decision_reason ? ` · ${rec.decision_reason}` : ""}
        </div>
        {deciding !== null && (
          <ReasonForm
            label="Причина решения"
            submitText={deciding ? "Принять" : "Отклонить"}
            danger={!deciding}
            pending={decide.isPending}
            error={decide.error}
            onSubmit={(reason) => decide.mutate(reason)}
            onCancel={() => setDeciding(null)}
          />
        )}
      </div>
      <div className="ref-side">
        <span className={`lv ${status.cls}`}>{status.label}</span>
        {rec.status === "open" && deciding === null && (
          <>
            <button type="button" className="linkbtn sub" onClick={() => setDeciding(true)}>
              Принять
            </button>
            <button type="button" className="linkbtn sub" onClick={() => setDeciding(false)}>
              Отклонить
            </button>
          </>
        )}
        {rec.status === "accepted" && !issued && (
          <button type="button" className="linkbtn sub" onClick={onIssue}>
            Выписать направление
          </button>
        )}
      </div>
    </div>
  );
}

function ReferralRow({ referral, patientId }: { referral: DoctorReferrals["referrals"][number]; patientId: string }) {
  const queryClient = useQueryClient();
  const [cancelling, setCancelling] = useState(false);
  const cancel = useMutation({
    mutationFn: (reason: string) => api.post(`/referrals/${referral.referral_id}/cancel`, { reason }),
    onSuccess: async () => {
      setCancelling(false);
      await queryClient.invalidateQueries({ queryKey: ["patient", patientId, "referrals"] });
    },
  });
  const issued = referral.status === "issued";
  return (
    <div className="ref">
      <div>
        <div style={{ fontWeight: 500 }}>
          {KIND[referral.kind]} · {referral.specialist}
        </div>
        <div>{referral.clinical_reason}</div>
        <div className="sub">
          {[
            URGENCY[referral.urgency],
            referral.category_label ?? (referral.category ? CATEGORY[referral.category] : null),
            referral.diagnosis_code,
            `выдано ${date(referral.issued_at)}`,
            `до ${date(referral.valid_until)}`,
          ]
            .filter(Boolean)
            .join(" · ")}
          {referral.cancel_reason ? ` · ${referral.cancel_reason}` : ""}
        </div>
        {cancelling && (
          <ReasonForm
            label="Причина отмены"
            submitText="Отменить направление"
            danger
            pending={cancel.isPending}
            error={cancel.error}
            onSubmit={(reason) => cancel.mutate(reason)}
            onCancel={() => setCancelling(false)}
          />
        )}
      </div>
      <div className="ref-side">
        <span className={`lv ${issued ? "lv-grn" : "lv-pln"}`}>{issued ? "выдано" : "отменено"}</span>
        {issued && !cancelling && (
          <button type="button" className="linkbtn sub" onClick={() => setCancelling(true)}>
            Отменить
          </button>
        )}
      </div>
    </div>
  );
}

function RecommendForm({ patientId, onDone }: { patientId: string; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [category, setCategory] = useState<"obligatory" | "desirable">("obligatory");
  const [reason, setReason] = useState("");
  const [submissionId] = useState(newSubmissionId);
  const save = useMutation({
    mutationFn: () => api.post(`/patients/${patientId}/referral-recommendations`, { category, reason: reason.trim(), submission_id: submissionId }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["patient", patientId, "referrals"] });
      onDone();
    },
  });
  const ok = reason.trim().length >= 3;
  return (
    <form
      className="pad ref-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (ok) save.mutate();
      }}
    >
      <span className="seg">
        {(["obligatory", "desirable"] as const).map((c) => (
          <button key={c} type="button" className={category === c ? "on" : ""} aria-pressed={category === c} onClick={() => setCategory(c)}>
            {CATEGORY[c]}
          </button>
        ))}
      </span>
      <div className="fld">
        <label htmlFor="rec-reason">Причина</label>
        <textarea id="rec-reason" className="inp" value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      {save.isError && <span className="form-err">{errorText(save.error)}</span>}
      <div className="ref-acts">
        <button type="button" className="btn btn-t" onClick={onDone}>
          Отменить
        </button>
        <button type="submit" className="btn btn-p" disabled={!ok || save.isPending}>
          Сохранить
        </button>
      </div>
    </form>
  );
}

function ReferralForm({ patientId, recommendation, onDone }: { patientId: string; recommendation: Rec | null; onDone: () => void }) {
  const queryClient = useQueryClient();
  const [kind, setKind] = useState<keyof typeof KIND>("consultation");
  const [specialist, setSpecialist] = useState("психиатр");
  const [urgency, setUrgency] = useState<keyof typeof URGENCY>("planned");
  const [category, setCategory] = useState<"" | "obligatory" | "desirable">(recommendation?.category ?? "");
  const [reason, setReason] = useState(recommendation?.reason ?? "");
  const [validUntil, setValidUntil] = useState(addDays(localDay(), 30));
  const [submissionId] = useState(newSubmissionId);
  const save = useMutation({
    mutationFn: () =>
      api.post(`/patients/${patientId}/referrals`, {
        kind,
        specialist: specialist.trim(),
        urgency,
        category: category || null,
        clinical_reason: reason.trim(),
        valid_until: validUntil,
        recommendation_id: recommendation?.recommendation_id ?? null,
        submission_id: submissionId,
      }),
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ["patient", patientId, "referrals"] });
      onDone();
    },
  });
  const ok = reason.trim().length >= 3 && specialist.trim() && validUntil;
  return (
    <form
      className="pad ref-form"
      onSubmit={(e) => {
        e.preventDefault();
        if (ok) save.mutate();
      }}
    >
      <div className="ref-grid">
        <div className="fld">
          <label htmlFor="rf-kind">Вид</label>
          <select id="rf-kind" className="inp" value={kind} onChange={(e) => setKind(e.target.value as keyof typeof KIND)}>
            {Object.entries(KIND).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <div className="fld">
          <label htmlFor="rf-spec">Специалист</label>
          <input id="rf-spec" className="inp" value={specialist} onChange={(e) => setSpecialist(e.target.value)} />
        </div>
        <div className="fld">
          <label htmlFor="rf-urg">Срочность</label>
          <select id="rf-urg" className="inp" value={urgency} onChange={(e) => setUrgency(e.target.value as keyof typeof URGENCY)}>
            {Object.entries(URGENCY).map(([k, v]) => (
              <option key={k} value={k}>
                {v}
              </option>
            ))}
          </select>
        </div>
        <div className="fld">
          <label htmlFor="rf-cat">Категория</label>
          <select id="rf-cat" className="inp" value={category} onChange={(e) => setCategory(e.target.value as typeof category)}>
            <option value="">—</option>
            <option value="obligatory">{CATEGORY.obligatory}</option>
            <option value="desirable">{CATEGORY.desirable}</option>
          </select>
        </div>
        <div className="fld">
          <label htmlFor="rf-until">Действует до</label>
          <DateInput id="rf-until" value={validUntil} min={localDay()} onChange={setValidUntil} />
        </div>
      </div>
      <div className="fld">
        <label htmlFor="rf-reason">Клиническое обоснование</label>
        <textarea id="rf-reason" className="inp" value={reason} onChange={(e) => setReason(e.target.value)} />
      </div>
      {save.isError && <span className="form-err">{errorText(save.error)}</span>}
      <div className="ref-acts">
        <button type="button" className="btn btn-t" onClick={onDone}>
          Отменить
        </button>
        <button type="submit" className="btn btn-p" disabled={!ok || save.isPending}>
          Выписать
        </button>
      </div>
    </form>
  );
}
