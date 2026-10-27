/** Small shared pieces of the therapy screens: warning lines with sources, segmented controls. */
import { useId, useState, type ReactNode } from "react";
import { Link } from "react-router";
import type { Source, Warning } from "../../../../api/types";
import { SeverityPill } from "../../../../ui/levels";
import { cleanQuote, quoted, sourceLabel } from "./model";
import "./therapy.css";

/** Quotes of a warning's sources; ОХЛП sources link to the full label in the knowledge base. */
export function SourceQuotes({ sources, names }: { sources: Source[]; names?: Map<string, string> }) {
  return (
    <>
      {sources.map((s, i) => (
        <div key={`${s.ref}-${s.section ?? ""}-${i}`} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="sub">
            {s.document === "ОХЛП" ? (
              <Link to={`/d/kb/drugs/${s.ref}`} style={{ color: "inherit" }}>
                {[s.document, names?.get(s.ref)].filter(Boolean).join(" ")}
                {s.section ? ` · ${s.section}` : ""}
              </Link>
            ) : (
              [s.document, s.ref, s.section].filter(Boolean).join(" · ")
            )}
          </span>
          {s.quote ? <blockquote>{quoted(cleanQuote(s.quote))}</blockquote> : <span className="sub">Цитата не найдена</span>}
        </div>
      ))}
    </>
  );
}

/** One warning: severity pill, message, source buttons that open the quotes in place (A05). */
export function WarningLine({
  warning,
  names,
  extra,
  pill,
}: {
  warning: Warning;
  names?: Map<string, string>;
  extra?: ReactNode;
  pill?: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const id = useId();
  return (
    <div className="w">
      {pill ?? <SeverityPill severity={warning.severity} />}
      <span>
        {warning.message}
        {warning.sources.length > 0 && " · "}
        {warning.sources.length > 0 && (
          <button type="button" className="srcbtn" aria-expanded={open} aria-controls={id} onClick={() => setOpen((v) => !v)}>
            {[...new Set(warning.sources.map(sourceLabel))].join(", ")}
          </button>
        )}
        {extra}
      </span>
      {open && (
        <div className="wq" id={id}>
          <SourceQuotes sources={warning.sources} names={names} />
        </div>
      )}
    </div>
  );
}

export function Seg<T extends string>({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: T | null;
  options: { value: T; label: string }[];
  onChange: (value: T) => void;
}) {
  return (
    <span className="seg" role="radiogroup" aria-label={label}>
      {options.map((o) => (
        <span
          key={o.value}
          role="radio"
          aria-checked={o.value === value}
          tabIndex={0}
          className={o.value === value ? "on" : undefined}
          onClick={() => onChange(o.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" || event.key === " ") {
              event.preventDefault();
              onChange(o.value);
            }
          }}
        >
          {o.label}
        </span>
      ))}
    </span>
  );
}
