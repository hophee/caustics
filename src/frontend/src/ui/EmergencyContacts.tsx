import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import { api } from "../api/client";
import { CloseButton, Dialog } from "./Dialog";
import { ErrorBox } from "./states";
import { formatContactPhone, normalizeContactPhone, type EmergencyContact } from "./contactPhone";
import "./EmergencyContacts.css";

const EMPTY: EmergencyContact = { last_name: "", first_name: "", middle_name: "", role: "", phone: "" };

export function EmergencyContacts({ patientId }: { patientId?: string }) {
  const [open, setOpen] = useState(false);
  const path = patientId ? `/patients/${patientId}/emergency-contacts` : "/me/emergency-contacts";
  const queryKey = patientId ? ["patients", patientId, "emergency-contacts"] : ["me", "emergency-contacts"];
  const contacts = useQuery({ queryKey, queryFn: () => api.get<EmergencyContact[]>(path) });
  return (
    <>
      <button type="button" className="btn emergency-contacts-button"
        disabled={Boolean(patientId) && !contacts.data?.length} onClick={() => setOpen(true)}>
        Экстренные контакты
      </button>
      {contacts.isError && <ErrorBox error={contacts.error} onRetry={() => void contacts.refetch()} />}
      {open && <ContactsDialog contacts={contacts.data} error={contacts.error} onRetry={() => void contacts.refetch()}
        readOnly={Boolean(patientId)} queryKey={queryKey} onClose={() => setOpen(false)} />}
    </>
  );
}

function ContactsDialog({ contacts, error, onRetry, readOnly, queryKey, onClose }: {
  contacts: EmergencyContact[] | undefined;
  error: Error | null;
  onRetry: () => void;
  readOnly: boolean;
  queryKey: string[];
  onClose: () => void;
}) {
  const id = useId();
  const queryClient = useQueryClient();
  const [adding, setAdding] = useState(false);
  const [form, setForm] = useState(EMPTY);
  const create = useMutation({
    mutationFn: () => api.post<EmergencyContact[]>("/me/emergency-contacts", form),
    onSuccess: (data) => {
      queryClient.setQueryData(queryKey, data);
      setAdding(false);
      setForm(EMPTY);
    },
  });
  const full = (contacts?.length ?? 0) >= 5;
  return (
    <Dialog name="emergency-contacts" labelledBy={id} onClose={() => { if (!create.isPending) onClose(); }}>
      <header><h2 id={id}>Экстренные контакты</h2><CloseButton onClick={() => { if (!create.isPending) onClose(); }} /></header>
      {error ? <ErrorBox error={error} onRetry={onRetry} /> : !contacts ? <p role="status">Загрузка…</p> : (
        <>
          {contacts.length === 0 && <p className="sub">Экстренные контакты пока не добавлены.</p>}
          <ul className="emergency-contact-list">
            {contacts.map((contact, index) => (
              <li key={index}>
                <strong>{[contact.last_name, contact.first_name, contact.middle_name].filter(Boolean).join(" ")}</strong>
                <span className="sub">{contact.role}</span>
                <a href={`tel:${contact.phone.startsWith("8") ? "+7" + contact.phone.slice(1) : contact.phone}`}>
                  {formatContactPhone(contact.phone)}
                </a>
              </li>
            ))}
          </ul>
          {!readOnly && <>
            <p className="sub">Добавлено {contacts.length} из 5 контактов.</p>
            {adding && !full ? (
              <form onSubmit={(event) => { event.preventDefault(); create.mutate(); }}>
                <h3>Добавить экстренный контакт</h3>
                <fieldset disabled={create.isPending}>
                  {([["last_name", "Фамилия"], ["first_name", "Имя"], ["middle_name", "Отчество (при наличии)"],
                    ["role", "Кем вам приходится?"]] as const).map(([key, label]) => (
                    <label key={key}>{label}
                      <input className="inp" required={key !== "middle_name"} maxLength={key === "role" ? 15 : 60}
                        pattern={key !== "middle_name" ? ".*\\S.*" : undefined}
                        value={form[key]} onChange={(event) => setForm({ ...form, [key]: event.target.value })} />
                    </label>
                  ))}
                  <label>Номер телефона
                    <input className="inp" type="tel" inputMode="tel" required placeholder="+7 (999) 123-45-67"
                      pattern="(?:\+7|8)[0-9]{10}" title="Введите 11 цифр, начиная с +7 или 8"
                      value={form.phone} onChange={(event) => setForm({ ...form, phone: normalizeContactPhone(event.target.value) })} />
                  </label>
                  {create.isError && <ErrorBox error={create.error} />}
                  <div className="emergency-contact-actions">
                    <button className="btn" type="button" onClick={() => { setAdding(false); create.reset(); }}>Отмена</button>
                    <button className="btn btn-p" type="submit">{create.isPending ? "Сохранение…" : "Сохранить"}</button>
                  </div>
                </fieldset>
              </form>
            ) : <button className="btn btn-p" type="button" disabled={full} onClick={() => setAdding(true)}>
              Добавить экстренный контакт
            </button>}
          </>}
        </>
      )}
    </Dialog>
  );
}
