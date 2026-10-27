import { Ellipsis } from "../../../../ui/Ellipsis";
import { EmergencyContacts } from "../../../../ui/EmergencyContacts";
/** Left profile box of the patient card (board I2-Summary `aside.card.prof`). */
import { useActiveCase, useCurrentTherapy, useDiagnoses, usePatientSummary, useProfile } from "../../../../api/queries";
import { age, date, dayMonth, sexLabel } from "../../../../lib/format";
import { Silhouette } from "../../../../ui/icons";
import { useDiagnosisName } from "./data";
import {
  SUICIDE_RISK,
  TRISTATE,
  UNKNOWN,
  caseText,
  comorbid,
  listText,
  mainCodes,
  pregnancyChip,
  showsPregnancy,
  therapyItem,
  therapyTitle,
  weightText,
} from "./view";

function Value({ text }: { text: string }) {
  return text === UNKNOWN ? <span className="chip nd">{text}</span> : <Ellipsis>{text}</Ellipsis>;
}

export function ProfileAside({ patientId }: { patientId: string }) {
  const summary = usePatientSummary(patientId).data;
  const profile = useProfile(patientId).data;
  const activeCase = useActiveCase(patientId).data;
  const therapy = useCurrentTherapy(patientId).data;
  const diagnoses = useDiagnoses(patientId, activeCase?.case_id).data;
  const mainCode = summary?.main_icd ?? null;
  const mainName = useDiagnosisName(mainCode).data;

  const birth = summary?.birth_date ?? profile?.birth_date;
  const sex = profile?.sex ?? summary?.sex;
  const weight = weightText(profile?.weight_kg);
  const others = profile ? comorbid(profile.diagnoses, mainCodes(diagnoses, mainCode)) : null;
  const chip = profile ? pregnancyChip(profile) : null;
  const items = therapy?.items.map((i) => therapyItem(i)) ?? null;

  return (
    <aside className="card prof">
      <div className="id">
        <div className="photo" role="img" aria-label="Фото не загружено">
          <Silhouette />
        </div>
        <div>
          <h1 className="nm"><Ellipsis>{summary?.full_name ?? "…"}</Ellipsis></h1>
          <div className="dx">
            {mainCode ? (
              <>
                <b style={{ fontWeight: 500 }}>{mainCode}</b>
                {mainName ? ` · ${mainName}` : ""}
              </>
            ) : (
              <span className="muted">Диагноз не установлен</span>
            )}
          </div>
        </div>
      </div>
      <dl className="kv" style={{ margin: 0 }}>
        <dt>Возраст</dt>
        <dd>{birth ? age(birth) : UNKNOWN}</dd>
        <dt>Дата рождения</dt>
        <dd>{birth ? date(birth) : UNKNOWN}</dd>
        <dt>Пол</dt>
        <dd>{sexLabel(sex)}</dd>
        <dt>Случай</dt>
        <dd>{activeCase === undefined ? "…" : caseText(activeCase)}</dd>
        {weight && (
          <>
            <dt>Вес</dt>
            <dd>{weight}</dd>
          </>
        )}
        <dt>Учётная запись</dt>
        <dd>{summary ? (summary.has_account ? "активна" : "не активирована") : "…"}</dd>
      </dl>
      {profile && (
        <>
          <details open>
            <summary>Аллергии и непереносимость</summary>
            <div className="dv">
              <div className="sm">
                <span className="muted">Аллергии</span>
                <Value text={listText(profile.allergies)} />
              </div>
              <div className="sm">
                <span className="muted">Непереносимость</span>
                <Value text={listText(profile.past_intolerance)} />
              </div>
              <div className="sm">
                <span className="muted">Вспом. вещества</span>
                <Value text={listText(profile.intolerant_excipients)} />
              </div>
              <div className="sm">
                <span className="muted">Неэффективность</span>
                <Value text={listText(profile.past_inefficacy)} />
              </div>
            </div>
          </details>
          {showsPregnancy(sex) && (
            <details>
              <summary>
                <span className="sm">
                  Беременность и лактация {chip && <span className={chip.cls}>{chip.text}</span>}
                </span>
              </summary>
              <div className="dv">
                <div className="sm">
                  <span className="muted">Беременность</span>
                  <TriValue value={profile.pregnant} />
                </div>
                <div className="sm">
                  <span className="muted">Лактация</span>
                  <TriValue value={profile.lactating} />
                </div>
              </div>
            </details>
          )}
          <details>
            <summary>Текущие препараты</summary>
            <div className="dv">
              {items === null ? (
                <span className="muted">…</span>
              ) : items.length === 0 ? (
                <span>{profile.current_medications_reviewed_at ? "нет" : <span className="chip nd">{UNKNOWN}</span>}</span>
              ) : (
                items.map((item) => (
                  <span key={item.key}>
                    {therapyTitle(item)}
                    {item.start ? `, с ${dayMonth(item.start)}` : ""}
                    {item.reported ? " · со слов пациента" : ""}
                  </span>
                ))
              )}
            </div>
          </details>
          <details>
            <summary>Сопутствующие диагнозы</summary>
            <div className="dv">
              <span>{others && others.length ? others.join(", ") : "нет"}</span>
            </div>
          </details>
          <details>
            <summary>ПАВ и суицидальный риск</summary>
            <div className="dv">
              <div className="sm">
                <span className="muted">Употребление ПАВ в анамнезе / Употребление ПАВ активное</span>
                <TriValue value={profile.substance_use} />
              </div>
              <div className="sm">
                <span className="muted">Суицидальный риск</span>
                {profile.suicide_risk === "unknown" ? (
                  <span className="chip nd">{SUICIDE_RISK.unknown}</span>
                ) : (
                  <span className={profile.suicide_risk === "high" ? "chip red" : undefined}>{SUICIDE_RISK[profile.suicide_risk]}</span>
                )}
              </div>
            </div>
          </details>
        </>
      )}
      <EmergencyContacts key={patientId} patientId={patientId} />
    </aside>
  );
}

function TriValue({ value }: { value: "yes" | "no" | "unknown" }) {
  if (value === "unknown") return <span className="chip nd">{TRISTATE.unknown}</span>;
  if (value === "yes") return <span className="chip red">да</span>;
  return <span>нет</span>;
}
