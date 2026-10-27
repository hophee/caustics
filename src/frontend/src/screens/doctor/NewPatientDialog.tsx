/**
 * Board D-NewPatient: the form (ФИО, дата рождения, пол, город → time zone) and, after
 * `POST /patients`, «Карточка создана» with the one-time invite code. `InviteDialog` shows a re-issued
 * code (`POST /patients/{id}/invite`) for a patient without an account.
 */
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { Link } from "react-router";
import { api } from "../../api/client";
import type { Schemas } from "../../api/client";
import { age, date, sexLabel } from "../../lib/format";
import { CloseButton, Dialog } from "../../ui/Dialog";
import { errorText } from "../../ui/states";
import { DateInput } from "../../ui/DateInput";
import { newPatientBody, validateNewPatient, type FormErrors, type NewPatientForm } from "./newPatient";
import "./NewPatientDialog.css";

type InviteIssued = Schemas["InviteIssued"];

function CopyIcon() {
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <rect x="9" y="9" width="11" height="11" rx="2" />
      <path d="M5 15V5a2 2 0 0 1 2-2h10" />
    </svg>
  );
}

function DoneIcon() {
  return (
    <svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="#247b50" strokeWidth="2.2" aria-hidden="true">
      <path d="M5 12l5 5 9-10" />
    </svg>
  );
}

export function InviteCode({ invite }: { invite: InviteIssued }) {
  const [copied, setCopied] = useState(false);
  return (
    <>
      <div className="ct">
        <h2>Код приглашения</h2>
      </div>
      <div className="code">
        <b>{invite.invite_code}</b>
        <button
          className="btn btn-t"
          type="button"
          onClick={() => {
            void navigator.clipboard
              ?.writeText(invite.invite_code)
              .then(() => setCopied(true))
              .catch(() => undefined);
          }}
        >
          <CopyIcon />
          {copied ? "Скопировано" : "Скопировать"}
        </button>
      </div>
      <div className="till sub">Одноразовый · действует до {date(invite.expires_at)}</div>
    </>
  );
}

const EMPTY: NewPatientForm = { lastName: "", firstName: "", middleName: "", birthDate: "", sex: "", timezone: "Europe/Moscow" };

export function NewPatientDialog({ onClose }: { onClose: () => void }) {
  const titleId = useId();
  const ids = useId();
  const queryClient = useQueryClient();
  const [form, setForm] = useState<NewPatientForm>(EMPTY);
  const [errors, setErrors] = useState<FormErrors>({});
  const create = useMutation({
    mutationFn: (body: ReturnType<typeof newPatientBody>) => api.post<InviteIssued>("/patients", body),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ["overview"] });
    },
  });
  const set = <K extends keyof NewPatientForm>(key: K, value: NewPatientForm[K]) => {
    setForm((current) => ({ ...current, [key]: value }));
    setErrors((current) => ({ ...current, [key]: undefined }));
  };
  const submit = () => {
    const found = validateNewPatient(form);
    setErrors(found);
    if (Object.values(found).some(Boolean)) return;
    create.mutate(newPatientBody(form));
  };
  const invite = create.data;

  return (
    <Dialog name="newpatient" className="sc" labelledBy={titleId} onClose={onClose}>
      <div className="mh">
        <h1 id={titleId}>Новый пациент</h1>
        <CloseButton onClick={onClose} />
      </div>
      {invite ? (
        <>
          <div className="done">
            <DoneIcon />
            Карточка создана
          </div>
          <dl className="kv2" style={{ margin: 0 }}>
            <dt>ФИО</dt>
            <dd>{[form.lastName.trim(), form.firstName.trim(), form.middleName.trim()].filter(Boolean).join(" ")}</dd>
            <dt>Дата рождения</dt>
            <dd>
              {date(form.birthDate)} · {age(form.birthDate)}
            </dd>
            <dt>Пол</dt>
            <dd>{sexLabel(form.sex)}</dd>
          </dl>
          <InviteCode invite={invite} />
          <div className="mf">
            <Link className="btn btn-t" to={`/d/patients/${invite.patient_id}`} onClick={onClose}>
              Открыть карту пациента
            </Link>
            <button className="btn btn-p" type="button" onClick={onClose}>
              Готово
            </button>
          </div>
        </>
      ) : (
        <form
          noValidate
          onSubmit={(event) => {
            event.preventDefault();
            submit();
          }}
        >
          <div className="form">
            {([["lastName", "Фамилия"], ["firstName", "Имя"], ["middleName", "Отчество (при наличии)"]] as const).map(([key, label]) => (
              <div className="fld" key={key}>
                <label htmlFor={`${ids}-${key}`}>{label}</label>
                <input id={`${ids}-${key}`} className="inp" autoComplete="off" maxLength={60}
                  required={key !== "middleName"} value={form[key]} aria-invalid={Boolean(errors[key])}
                  aria-describedby={errors[key] ? `${ids}-${key}-error` : undefined}
                  onChange={(event) => set(key, event.target.value)} />
                {errors[key] && <span id={`${ids}-${key}-error`} className="err">{errors[key]}</span>}
              </div>
            ))}
            <div className="fld">
              <label htmlFor={`${ids}-birth`}>Дата рождения</label>
              <DateInput
                id={`${ids}-birth`}
                value={form.birthDate}
                aria-invalid={Boolean(errors.birthDate)}
                onChange={(value) => set("birthDate", value)}
              />
              {errors.birthDate && <span className="err">{errors.birthDate}</span>}
            </div>
            <div className="fld">
              <span className="lbl" id={`${ids}-sex`}>
                Пол
              </span>
              <span className="seg" role="radiogroup" aria-labelledby={`${ids}-sex`} style={{ alignSelf: "flex-start" }}>
                {(["female", "male"] as const).map((sex) => (
                  <span
                    key={sex}
                    role="radio"
                    aria-checked={form.sex === sex}
                    tabIndex={0}
                    className={form.sex === sex ? "on" : undefined}
                    onClick={() => set("sex", sex)}
                    onKeyDown={(event) => {
                      if (event.key === " " || event.key === "Enter") {
                        event.preventDefault();
                        set("sex", sex);
                      }
                    }}
                  >
                    {sex === "female" ? "Женский" : "Мужской"}
                  </span>
                ))}
              </span>
              {errors.sex && <span className="err">{errors.sex}</span>}
            </div>
            {create.isError && (
              <div role="alert" className="err">
                {errorText(create.error)}
              </div>
            )}
          </div>
          <div className="mf">
            <button className="btn btn-t" type="button" onClick={onClose}>
              Отмена
            </button>
            <button className="btn btn-p" type="submit" disabled={create.isPending}>
              Создать карту пациента
            </button>
          </div>
        </form>
      )}
    </Dialog>
  );
}

/** A fresh invite for a patient who has not activated an account yet. */
export function InviteDialog({
  patient,
  onClose,
}: {
  patient: { patient_id: string; full_name: string; birth_date: string };
  onClose: () => void;
}) {
  const titleId = useId();
  const issue = useMutation({
    mutationFn: () => api.post<InviteIssued>(`/patients/${patient.patient_id}/invite`),
  });
  return (
    <Dialog name="newpatient" className="sc" labelledBy={titleId} onClose={onClose}>
      <div className="mh">
        <h1 id={titleId}>{patient.full_name}</h1>
        <CloseButton onClick={onClose} />
      </div>
      <dl className="kv2" style={{ margin: 0 }}>
        <dt>Дата рождения</dt>
        <dd>
          {date(patient.birth_date)} · {age(patient.birth_date)}
        </dd>
      </dl>
      {issue.data ? (
        <InviteCode invite={issue.data} />
      ) : (
        issue.isError && (
          <div role="alert" className="err form">
            {errorText(issue.error)}
          </div>
        )
      )}
      <div className="mf">
        {issue.data ? (
          <button className="btn btn-p" type="button" onClick={onClose}>
            Готово
          </button>
        ) : (
          <>
            <button className="btn btn-t" type="button" onClick={onClose}>
              Отмена
            </button>
            <button className="btn btn-p" type="button" disabled={issue.isPending} onClick={() => issue.mutate()}>
              Выдать новый код
            </button>
          </>
        )}
      </div>
    </Dialog>
  );
}
