import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useMemo, useState } from "react";
import { Link, useNavigate, useParams, useSearchParams } from "react-router";
import { api } from "../../../api/client";
import { keys, useActiveCase, useDiagnoses, useProfile } from "../../../api/queries";
import type { CaseOut, Profile, ScaleListItem, Visit } from "../../../api/types";
import { Dialog } from "../../../ui/Dialog";
import { dayMonth } from "../../../lib/format";
import { useScreen } from "../../../ui/screen";
import { ErrorBox, Loading } from "../../../ui/states";
import { DateTimeInput } from "../../../ui/DateInput";
import { useResults, useVisits } from "./parts/data";
import { useCurrent, usePrescriptions } from "./therapy/api";
import { joinCurrent } from "./therapy/current";
import { PatientFrame } from "./therapy/PatientFrame";
import { doseText, normalizeRegimen, periodText } from "./therapy/regimen";
import { AssignForm, AssignmentsCard } from "./Scales";
import { useEncounter } from "./visit/store";
import { completionErrors, emptyEncounter, nextAfterTests, type Encounter, type VisitStep } from "./visit/model";
import { PatientProfile } from "./visit/PatientProfile";
import { CurrentMedicationEditor, persistMedicationDraft, useMedicationDraft } from "./visit/CurrentMedicationEditor";
import { Examination } from "./visit/Examination";
import { DiagnosisSearch } from "./visit/SearchSelect";
import { Investigations } from "./visit/Investigations";
import { recommendedInvestigations } from "./visit/investigationCatalog";
import { Dynamics } from "./visit/Dynamics";
import "./VisitWorkspace.css";

const STEPS: [VisitStep, string][] = [["record", "Карточка и запись"], ["tests", "Тесты"], ["diagnosis", "Диагноз"], ["labs", "Анализы"], ["decision", "Лечение"], ["summary", "Итог"]];
export function VisitWorkspace() {
  useScreen("visit");
  const {patientId = ""} = useParams();
  const client = useQueryClient(); const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const step = STEPS.find(([id]) => id === params.get("step"))?.[0] ?? "record";
  const [visit, setVisit] = useEncounter(patientId);
  const [medicationDraft, setMedicationDraft] = useMedicationDraft(patientId);
  const [assigning, setAssigning] = useState(false);
  const [showLabs, setShowLabs] = useState(false);
  const active = useActiveCase(patientId); const profile = useProfile(patientId);
  const openCase = useMutation({
    mutationFn: () => api.post<CaseOut>(`/patients/${patientId}/cases`),
    onSuccess: (created) => {
      client.setQueryData(keys.activeCase(patientId), created);
      void client.invalidateQueries({queryKey: keys.patient(patientId)});
      void client.invalidateQueries({queryKey: ["overview"]});
    },
  });
  const caseId = active.data?.case_id;
  const diagnoses = useDiagnoses(patientId, caseId);
  const main = diagnoses.data?.find((d) => d.is_main);
  const history = useVisits(patientId, caseId);
  const patch = (p: Partial<Encounter>) => setVisit((v) => ({...v, ...p}));
  useEffect(() => {
    if (!visit.profile && profile.data && history.data && diagnoses.data) {
      const draft = history.data.find((v) => v.content.schema === "encounter-v1" && v.content.status === "draft");
      if (draft) setVisit({...emptyEncounter(), ...draft.content, id: draft.visit_id} as Encounter);
      else setVisit((v) => ({...v, profile: profile.data, kind: history.data.length ? "repeat" : v.kind,
        diagnosis: main?.icd_code ?? "", diagnosisSaved: Boolean(main), testFilter: main?.icd_code ?? "",
        lifeHistory: String(history.data?.find((x) => x.content.lifeHistory)?.content.lifeHistory ?? ""),
        examination: {"Масса, кг": profile.data.weight_kg === null ? "" : String(profile.data.weight_kg)},
        investigations: (history.data?.find((x) => Array.isArray(x.content.investigations))?.content.investigations ?? []) as Encounter["investigations"],
      }));
    }
  }, [visit.profile, profile.data, history.data, diagnoses.data, main, setVisit]);
  const go = (s: VisitStep) => setParams({step: s}, {replace: true});
  const save = useMutation({mutationFn: async (completed: boolean) => {
    if (!caseId) throw new Error("Нет активного случая");
    if (medicationDraft) {
      await persistMedicationDraft(patientId, medicationDraft);
      setMedicationDraft(null);
      await client.invalidateQueries({queryKey: keys.patient(patientId)});
    }
    const content = {...visit, schema: "encounter-v1", status: completed ? "completed" : "draft"};
    const body = {kind: visit.kind, held_at: new Date(visit.heldAt).toISOString(), content};
    if (visit.profile) {
      const updated = await api.put<Profile>(`/patients/${patientId}/profile`, {...visit.profile, suicide_risk: visit.risk ?? visit.profile.suicide_risk});
      client.setQueryData(keys.profile(patientId), updated);
    }
    const result = visit.id ? await api.put<Visit>(`/cases/${caseId}/visits/${visit.id}`, body) : await api.post<Visit>(`/cases/${caseId}/visits`, body);
    return {id: result.visit_id, status: content.status as Encounter["status"]};
  }, onSuccess: (data) => {patch(data); void client.invalidateQueries({queryKey: keys.patient(patientId)});} });
  const next = async (s: VisitStep) => {try {await save.mutateAsync(false); go(s);} catch { /* error displayed below */ }};
  const diagnosisSave = useMutation({mutationFn: () => api.post(`/cases/${caseId}/diagnoses`, {icd_code: visit.diagnosis, is_main: true, provisional: visit.provisional}),
    onSuccess: () => {patch({diagnosisSaved: true, profile: visit.profile ? {...visit.profile, diagnoses: [...visit.profile.diagnoses.filter((code) => code !== main?.icd_code && code !== visit.diagnosis), visit.diagnosis]} : undefined, investigations: visit.investigationDiagnosis === visit.diagnosis ? visit.investigations : recommendedInvestigations(visit.diagnosis), investigationDiagnosis: visit.diagnosis}); void client.invalidateQueries({queryKey: keys.patient(patientId)}); go("labs");}});
  const openTherapy = async (mode: "prescription" | "dose" | "diagnosis") => {
    try { await save.mutateAsync(false); navigate(`/d/patients/${patientId}/draft?visit=1&mode=${mode}${mode === "dose" ? "" : "&pick=1"}`); } catch { /* save error */ }
  };
  const field = (key: "reason" | "complaints" | "diseaseHistory" | "lifeHistory" | "rationale" | "uncertainty" | "recommendations" | "worsening" | "diary", label: string) =>
    <div className="fld"><label htmlFor={`visit-${key}`}>{label}</label><textarea id={`visit-${key}`} className="inp" value={visit[key]} onChange={(e) => patch({[key]: e.target.value})} /></div>;
  if (active.isPending || profile.isPending || (caseId && history.isPending)) return <Loading />;
  if (active.isError || profile.isError || history.isError) return <ErrorBox error={active.error ?? profile.error ?? history.error} />;
  if (!caseId) return <PatientFrame patientId={patientId} crumb="Приём">
    <Dialog name="newpatient" labelledBy="open-case-title" onClose={() => navigate(`/d/patients/${patientId}`)}>
      <div className="mh"><h2 id="open-case-title">Открыть новый медицинский случай?</h2></div>
      {openCase.isError && <ErrorBox error={openCase.error} />}
      <div className="mf"><button className="btn btn-t" disabled={openCase.isPending} onClick={() => navigate(`/d/patients/${patientId}`)}>Отмена</button>
        <button className="btn btn-p" disabled={openCase.isPending} onClick={() => openCase.mutate()}>Да</button></div>
    </Dialog>
  </PatientFrame>;
  const errors = completionErrors(visit);
  return <PatientFrame patientId={patientId} crumb={`${visit.kind === "first" ? "Первичный" : "Повторный"} приём`}>
    {visit.status === "completed" ? <section className="card pad"><h2>Приём завершён</h2><p>Запись сохранена. Назначения и история доступны в карточке.</p>
      <button className="btn btn-p" onClick={() => {setVisit({...emptyEncounter(), kind: "repeat"}); go("record");}}>Начать новый повторный приём</button>
      <Link className="btn btn-t" to={`/d/patients/${patientId}`}>К карточке пациента</Link></section> : <>
      <nav className="stp" aria-label="Шаги приёма">{STEPS.filter(([id]) => visit.kind === "first" || visit.action === "diagnosis" || id !== "diagnosis").map(([id, name]) =>
        <button key={id} type="button" className={step === id ? "on" : ""} onClick={() => go(id)}>{name}</button>)}</nav>
      <div className="visit-workspace">
      {step === "record" && <>
        <section className="card pad"><h2>Приём</h2><div className="visit-fields"><label className="fld">Вид приёма<select className="inp" value={visit.kind} onChange={(e) => patch({kind: e.target.value as Encounter["kind"], action: undefined, therapyConfirmed: false})}><option value="first">Первичный</option><option value="repeat">Повторный</option></select></label>
          <label className="fld">Дата и время<DateTimeInput value={visit.heldAt} onChange={(heldAt) => heldAt && patch({heldAt})} /></label></div>{field("reason", "Причина обращения")}</section>
        {visit.profile && <PatientProfile value={visit.profile} main={main?.icd_code ?? ""} onChange={(p) => patch({profile: p})} />}
        <CurrentMedicationEditor patientId={patientId} />
        {visit.kind === "repeat" && <Dynamics patientId={patientId} diagnosis={visit.diagnosis} />}
        <section className="card pad"><h2>Жалобы и анамнез</h2><label><input type="checkbox" checked={visit.noComplaints} onChange={(e) => patch({noComplaints: e.target.checked})} /> Жалоб нет</label>
          {!visit.noComplaints && field("complaints", "Жалобы")}{field("diseaseHistory", visit.kind === "first" ? "Анамнез болезни" : "Течение заболевания с прошлого визита")}
          {visit.kind === "first" ? field("lifeHistory", "Анамнез жизни") : <details><summary>Анамнез жизни · дополнить при необходимости</summary>{field("lifeHistory", "Анамнез жизни")}</details>}
        </section>
        <Examination values={visit.examination} onChange={(v) => patch({examination: v, profile: visit.profile ? {...visit.profile,
          weight_kg: v["Масса, кг"] ? Number(v["Масса, кг"]?.replace(",", ".")) : visit.profile.weight_kg} : undefined})} />
        <button className="btn btn-p" disabled={save.isPending} onClick={() => void next("tests")}>Далее · тесты</button>
      </>}
      {step === "tests" && <><Tests patientId={patientId} filter={visit.testFilter ?? ""} onFilter={(testFilter) => patch({testFilter})} />
        <button className="btn btn-p" onClick={() => void next(nextAfterTests(visit.kind, visit.action))}>Далее / пропустить тесты</button></>}
      {step === "diagnosis" && <section className="card pad"><h2>Основной диагноз</h2>
        <DiagnosisSearch value={visit.diagnosis} onChange={(diagnosis) => patch({diagnosis, diagnosisSaved: false})} onClear={() => patch({diagnosis: "", diagnosisSaved: false})} />
        <p>Выбран: {visit.diagnosis || "не выбран"}</p><label><input type="checkbox" checked={visit.provisional} onChange={(e) => patch({provisional: e.target.checked, diagnosisSaved: false})} /> Предварительный диагноз</label>
        {field("rationale", "Обоснование диагноза")}{field("uncertainty", "Диагностические сомнения, если имеются")}
        {diagnosisSave.isError && <ErrorBox error={diagnosisSave.error} />}<button className="btn btn-p" disabled={!visit.diagnosis || diagnosisSave.isPending} onClick={() => diagnosisSave.mutate()}>Сохранить диагноз и продолжить</button>
      </section>}
      {step === "labs" && <><section className="card pad"><h2>Дополнительная диагностика</h2><p>Во вкладке «Анализы» отмечены исследования, рекомендованные КР при {visit.diagnosis || "выбранном диагнозе"}. Перейти к подбору препаратов без дополнительной диагностики?</p>
        <button className="btn btn-t" onClick={() => setShowLabs(true)}>Открыть анализы</button> <button className="btn btn-p" onClick={() => void next("decision")}>Перейти</button></section>
        {showLabs && <Investigations items={visit.investigations} onChange={(investigations) => patch({investigations})} />}</>}
      {step === "decision" && <><section className="card pad"><h2>{visit.kind === "first" ? "Подбор терапии" : "Хотите изменить назначение?"}</h2>
        {visit.kind === "repeat" && visit.action !== "diagnosis" && <div className="visit-actions">{([ ["continue", "Нет, продолжить лечение"], ["prescription", "Изменить назначение"], ["dose", "Изменить дозировку"], ["diagnosis", "Изменить диагноз"] ] as const).map(([action, label]) =>
          <button key={action} className={`btn ${visit.action === action ? "btn-p" : "btn-t"}`} onClick={() => {patch({action, therapyConfirmed: false}); if (action === "diagnosis") go("tests");}}>{label}</button>)}</div>}
        <label className="fld">Суицидальный риск<select className="inp" value={visit.risk ?? ""} onChange={(e) => patch({risk: (e.target.value || undefined) as Encounter["risk"]})}>
          <option value="">Выберите оценку</option><option value="low">Низкий</option><option value="high">Высокий</option></select></label>
        <RiskTest patientId={patientId} diagnosis={visit.diagnosis} />
        {!visit.diagnosisSaved && <p role="alert">Перед подбором сохраните основной диагноз.</p>}
        {visit.kind === "first" && <button className="btn btn-t" onClick={() => patch({action: "prescription"})}>Назначить препараты</button>}
        {visit.kind === "first" && <button className="btn btn-t" onClick={() => patch({action: "continue"})}>Без изменения лекарственных назначений</button>}
        {visit.action && <button className="btn btn-p" disabled={!visit.risk || !visit.diagnosisSaved || save.isPending} onClick={() => {if (visit.action === "continue") void next("summary"); else if (visit.action) void openTherapy(visit.action);}}>{visit.action === "continue" ? "Далее · итог приёма" : visit.action === "dose" ? "Открыть текущую аптечку" : "Открыть подбор препаратов"}</button>}
      </section><CurrentPlan patientId={patientId} /></>}
      {step === "summary" && <>
        <CurrentPlan patientId={patientId} />
        <section className="card pad"><h2>План после решения по терапии</h2><p>Тесты на дом назначаются здесь, после выбора лечения.</p>
          <button className="btn btn-t" disabled={!visit.action || (visit.action !== "continue" && !visit.therapyConfirmed)} onClick={() => setAssigning(!assigning)}>Назначить шкалу на дом</button>
          {assigning && <AssignForm patientId={patientId} caseId={caseId} onDone={() => setAssigning(false)} />}
          <AssignmentsCard patientId={patientId} caseId={caseId} />
          {field("diary", "Дневники: что отслеживать, срок и частота")}{field("recommendations", "Рекомендации, консультации и немедикаментозная помощь")}
          <label className="fld">Следующий приём<input className="inp" type="date" value={visit.nextVisit} onChange={(e) => patch({nextVisit: e.target.value})} /></label>
          {field("worsening", "Инструкции при ухудшении состояния")}
        </section>
        <details className="card pad"><summary>Анализы и результаты · необязательно</summary><Investigations items={visit.investigations} onChange={(investigations) => patch({investigations})} /></details>
        <section className="card pad"><h2>Итоговая запись для врача</h2><p>{visit.diagnosis} · {visit.provisional ? "предварительный" : "установленный"} · риск: {visit.risk === "high" ? "высокий" : visit.risk === "low" ? "низкий" : "не оценён"}</p>
          <p>Жалобы: {visit.noComplaints ? "отрицает" : visit.complaints || "не заполнено"}</p><p>{visit.diseaseHistory}</p><p>{visit.rationale}</p>
          <p>Решение по терапии: {visit.action === "continue" ? "продолжить" : visit.action === "dose" ? "изменить дозировку" : visit.action === "diagnosis" ? "изменить диагноз и лечение" : "изменить назначение"}.</p>
          {errors.map((e) => <p role="alert" key={e}>{e}</p>)}<button className="btn btn-p" disabled={errors.length > 0 || save.isPending} onClick={() => save.mutate(true)}>Завершить приём</button>
        </section>
        <details className="card pad"><summary>Предпросмотр плана для пациента</summary><div className="visit-patient-preview">
          <h2>План после приёма</h2><CurrentPlan patientId={patientId} />
          {visit.investigations.length > 0 && <><h3>Исследования</h3><ul>{visit.investigations.map((i) => <li key={i.name}>{i.name}{i.due && ` · до ${i.due}`}</li>)}</ul></>}
          {visit.recommendations && <p>{visit.recommendations}</p>}{visit.diary && <p>Дневники: {visit.diary}</p>}
          {visit.nextVisit && <p>Следующий приём: {visit.nextVisit}</p>}{visit.worsening && <p>{visit.worsening}</p>}
        </div><p className="sub">Предпросмотр: оценки шкал, оценка риска и заметки врача сюда не включены.</p></details>
      </>}
      {save.isError && <ErrorBox error={save.error} />}
      <footer className="visit-actions"><button className="btn btn-t" disabled={save.isPending} onClick={() => save.mutate(false)}>Сохранить черновик приёма</button><span className="sub">{save.isPending ? "Сохранение…" : save.isSuccess ? "Запись сохранена на сервере" : "Изменения сохраняются в этой вкладке до отправки"}</span></footer>
      </div>
    </>}
  </PatientFrame>;
}

type ListedScale = ScaleListItem & {diagnoses: string[]; source: string};
const applies = (code: string, diagnoses: string[]) => diagnoses.some((d) => code.startsWith(d.replace(/\.x$/, "")));
function Tests({patientId, filter, onFilter}: {patientId: string; filter: string; onFilter: (s: string) => void}) {
  const scales = useQuery({queryKey: ["kb", "scales"], queryFn: () => api.get<ListedScale[]>("/scales"), staleTime: Infinity});
  const results = useResults(patientId);
  return <section className="card pad"><h2>Тесты на приёме</h2><p>Диагноз в поиске только фильтрует тесты. Этот шаг можно пропустить.</p>
    <DiagnosisSearch value={filter} onChange={onFilter} onClear={() => onFilter("")} />
    {scales.isError && <ErrorBox error={scales.error} />}
    {(scales.data ?? []).filter((s) => !filter || applies(filter, s.diagnoses)).map((s) => {
      const today = (results.data?.items ?? []).find((r) => r.scale_code === s.code && new Date(r.taken_at).toDateString() === new Date().toDateString());
      return <div className="win" key={s.code}><div><b>{s.name}</b><div className="sub">{s.filled_by === "clinician" ? "Заполняет врач" : "Ответы пациента на приёме"} · {s.items} пунктов</div></div>
        {today ? <span>Уже заполнена сегодня · {dayMonth(today.taken_at)}</span> : <Link to={`/d/patients/${patientId}/rating/${encodeURIComponent(s.code)}`} state={{back: `/d/patients/${patientId}/visit?step=tests`}}>Заполнить</Link>}</div>;
    })}
  </section>;
}
function RiskTest({patientId, diagnosis}: {patientId: string; diagnosis: string}) {
  const scales = useQuery({queryKey: ["kb", "scales"], queryFn: () => api.get<ListedScale[]>("/scales"), staleTime: Infinity});
  const scale = scales.data?.find((s) => s.code === "C-SSRS" && applies(diagnosis, s.diagnoses));
  return scale ? <Link to={`/d/patients/${patientId}/rating/${encodeURIComponent(scale.code)}`} state={{back: `/d/patients/${patientId}/visit?step=decision`}}>Оценить по {scale.name}</Link> : null;
}
export function CurrentPlan({patientId}: {patientId: string}) {
  const current = useCurrent(patientId); const versions = usePrescriptions(patientId, 50);
  const views = useMemo(() => joinCurrent(current.data?.items ?? [], versions.data?.items ?? []), [current.data, versions.data]);
  return <section className="card pad"><h2>Текущая аптечка</h2>{current.isPending && <Loading />}{current.isError && <ErrorBox error={current.error} />}
    {current.data && !views.length && <p>Препараты не назначены</p>}
    {views.map((v) => {const r = normalizeRegimen(v.regimen); return <div className="pv" key={v.ref}><b>{v.substance ?? v.item?.substance ?? "МНН не указано"}</b><p>{[doseText(r), periodText(r)].filter(Boolean).join(" · ")}</p></div>;})}
  </section>;
}
