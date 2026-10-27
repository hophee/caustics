/** «Пропустил» / «Принял» for one dose; a skip asks for a reason first (boards I2-Today, P-Meds). */
import { useId, useState } from "react";
import type { Dose } from "../../api/types";
import { ErrorBox } from "../../ui/states";
import { doseStateLabel } from "./doses";
import { useAdherenceOptions, useMarkDose } from "./patientApi";
import { ReactionQuestion } from "./ReactionQuestion";

export function DoseMarker({ dose }: { dose: Dose }) {
  const options = useAdherenceOptions();
  const mark = useMarkDose();
  const [skipping, setSkipping] = useState(false);
  const [reason, setReason] = useState<string | null>(null);
  const [reactionOpen, setReactionOpen] = useState(false);
  const titleId = useId();

  const status = dose.mark?.status ?? null;
  const label = doseStateLabel(dose.state);
  // A dose past the marking window keeps its state; nothing to press.
  if (!dose.can_mark && label) return <div className="marked">{label}</div>;

  const disabled = !dose.can_mark || mark.isPending;
  const skipOn = skipping || status === "skipped";
  const takenOn = !skipping && status === "taken";
  const anyOn = skipOn || takenOn;

  return (
    <>
      <div className="two">
        <button
          type="button"
          className={`btn ${skipOn ? "sel" : "btn-s"}`}
          aria-pressed={skipOn}
          disabled={disabled}
          title={dose.why_not ?? undefined}
          onClick={() => {
            setSkipping(true);
            setReason(dose.mark?.reason ?? null);
          }}
        >
          Пропустил
        </button>
        <button
          type="button"
          className={`btn ${takenOn ? "sel" : anyOn ? "btn-s" : "btn-p"}`}
          aria-pressed={takenOn}
          disabled={disabled}
          title={dose.why_not ?? undefined}
          onClick={() => {
            setSkipping(false);
            mark.mutate({ dose, status: "taken" });
          }}
        >
          Принял
        </button>
      </div>
      {skipping && (
        <div className="reasons" role="radiogroup" aria-labelledby={titleId}>
          <h2 id={titleId}>Почему пропустили?</h2>
          {(options.data?.skip_reasons ?? []).map((r) => (
            <button
              key={r.code}
              type="button"
              role="radio"
              className={`opt${reason === r.code ? " on" : ""}`}
              aria-checked={reason === r.code}
              onClick={() => { setReason(r.code); setReactionOpen(r.code === "intolerance"); }}
            >
              {r.label}
            </button>
          ))}
          <button
            type="button"
            className="btn btn-p"
            style={{ marginTop: 4 }}
            disabled={!reason || mark.isPending}
            onClick={() => {
              if (!reason) return;
              mark.mutate({ dose, status: "skipped", reason }, { onSuccess: () => setSkipping(false) });
            }}
          >
            Сохранить
          </button>
        </div>
      )}
      {reactionOpen && <ReactionQuestion tradeName={dose.name} />}
      {options.isError && <ErrorBox error={options.error} onRetry={() => void options.refetch()} />}
      {mark.isError && <ErrorBox error={mark.error} />}
    </>
  );
}
