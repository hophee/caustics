import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../../../api/client";
import { ErrorBox, Loading } from "../../../../ui/states";
import { useCurrent, type CurrentTherapyOut } from "../therapy/api";
import { useStored } from "../therapy/store";
import { useDebounced } from "../therapy/hooks";
import { SearchSelect } from "./SearchSelect";

interface Medication { ref?: string; label_id: string | null; substance: string }
interface MedicationDraft {base_token: string; items: Medication[]}
interface MedicationChoice extends Medication {code: string; name: string}
const normalize = (s: string) => s.toLocaleLowerCase("ru").replace(/ё/g, "е").trim();
export function useMedicationDraft(patientId: string) {
  return useStored<MedicationDraft | null>(`kompas.medications.${patientId}`, null,
    (v): v is MedicationDraft | null => v === null || typeof v === "object" && v !== null &&
      "base_token" in v && typeof v.base_token === "string" && "items" in v && Array.isArray(v.items));
}
export function persistMedicationDraft(patientId: string, draft: MedicationDraft) {
  return api.put<CurrentTherapyOut>(`/patients/${patientId}/therapy/current`, draft);
}

export function CurrentMedicationEditor({patientId}: {patientId: string}) {
  const client = useQueryClient();
  const current = useCurrent(patientId);
  const [draft, setDraft] = useMedicationDraft(patientId);
  const [query, setQuery] = useState("");
  const [editing, setEditing] = useState<number | null>(null);
  const [feedback, setFeedback] = useState("");
  const [limit, setLimit] = useState(50);
  const q = useDebounced(query.trim(), 200);
  const labels = useQuery({queryKey: ["kb", "current-medication-search", "full", q, limit], enabled: q.length > 0,
    queryFn: () => api.get<{items: MedicationChoice[]; total: number}>("/medications/search", {q, limit})});
  const choices = q === query.trim() && q ? labels.data?.items ?? [] : [];
  const items = draft?.items ?? (current.data?.items ?? []).map((i) => ({ref: i.ref, label_id: i.label_id, substance: i.substance ?? "МНН не указано"}));
  const change = (next: Medication[]) => {
    if (!current.data) return;
    setDraft({base_token: draft?.base_token ?? current.data.base_token, items: next});
    setFeedback("");
  };
  const save = useMutation({mutationFn: () => persistMedicationDraft(patientId, draft ?? {base_token: current.data?.base_token ?? "", items}),
    onSuccess: async () => {
      setDraft(null); setFeedback("Текущая терапия сохранена по МНН. Проверки обновлены.");
      await client.invalidateQueries({queryKey: ["patient", patientId]});
    }});
  return <section className="card pad"><h2>Текущая терапия</h2>
    <p className="sub">Препараты, которые пациент принимает сейчас. Поиск по МНН или торговому названию; в списке сохраняется МНН.</p>
    {current.isPending && <Loading />}{current.isError && <ErrorBox error={current.error} />}
    {current.data && <>
      {!items.length && <p>В списке нет препаратов.</p>}
      {items.map((item, i) => <div className="win" key={item.ref ?? `new-${i}`}>
        <b>{item.substance}</b><div>
          <button type="button" className="linkbtn" disabled={save.isPending} onClick={() => {setEditing(i); setQuery("");}}>Заменить</button>{" · "}
          <button type="button" className="linkbtn" disabled={save.isPending} onClick={() => {change(items.filter((_, n) => n !== i)); setEditing(null);}}>Убрать из текущей терапии</button>
        </div></div>)}
      <SearchSelect label={editing === null ? "Добавить текущий препарат: МНН или торговое название" : "Заменить препарат: МНН или торговое название"}
        showCode={false} value={query} options={choices} onQuery={(value) => {setQuery(value); setLimit(50);}} onPick={(option) => {
          const chosen = choices.find((c) => c.code === option.code);
          if (!chosen || save.isPending) return;
          const duplicate = items.some((item, i) => i !== editing && normalize(item.substance) === normalize(chosen.substance));
          if (duplicate) {setFeedback("Это МНН уже есть в текущей терапии."); return;}
          change(editing === null ? [...items, {label_id: chosen.label_id, substance: chosen.substance}] : items.map((item, i) => i === editing ? {label_id: chosen.label_id, substance: chosen.substance, ref: item.ref} : item));
          setEditing(null); setQuery("");
        }} />
      {editing !== null && <button type="button" className="linkbtn" onClick={() => {setEditing(null); setQuery("");}}>Отменить замену</button>}
      {labels.isFetching && query && <p role="status">Поиск…</p>}
      {query && labels.data && labels.data.total > choices.length && <p className="sub">
        Показано {choices.length} из {labels.data.total}. {limit < 500
          ? <button type="button" className="linkbtn" onClick={() => setLimit((n) => Math.min(n + 50, 500))}>Показать больше</button>
          : "Уточните название для поиска."}
      </p>}
      {labels.isError && <ErrorBox error={labels.error} />}
      <button type="button" className="btn btn-t" disabled={save.isPending} onClick={() => save.mutate()}>
        {save.isPending ? "Сохранение…" : "Сохранить текущую терапию"}</button>
      {draft && <p className="sub">Изменения также сохранятся при сохранении профиля, приёма или переходе кнопкой «Далее».</p>}
      {draft && <button type="button" className="linkbtn" disabled={save.isPending} onClick={() => {setDraft(null); setEditing(null); setFeedback("");}}>Сбросить изменения списка</button>}
      {save.isError && <ErrorBox error={save.error} />}{feedback && <p role="status">{feedback}</p>}
    </>}
  </section>;
}
