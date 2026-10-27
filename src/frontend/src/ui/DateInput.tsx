/**
 * Date and time fields typed as дд.мм.гггг / чч:мм. Native date and time inputs follow the browser's
 * locale (mm/dd/yyyy, AM/PM), so these are text fields with a mask. Values stay ISO, as the native
 * inputs gave them: "1992-03-01", "08:30", "2026-10-25T08:30"; "" while the field is empty or incomplete.
 */
import { useState, type InputHTMLAttributes } from "react";

type Props = Omit<InputHTMLAttributes<HTMLInputElement>, "value" | "onChange" | "type" | "min" | "max"> & {
  value: string;
  onChange: (value: string) => void;
  /** ISO bounds, compared as strings. */
  min?: string;
  max?: string;
};

const digits = (text: string, n: number) => text.replace(/\D/g, "").slice(0, n);

function maskDate(text: string): string {
  const d = digits(text, 8);
  return [d.slice(0, 2), d.slice(2, 4), d.slice(4)].filter(Boolean).join(".");
}

function maskTime(text: string): string {
  const d = digits(text, 4);
  return [d.slice(0, 2), d.slice(2)].filter(Boolean).join(":");
}

function maskDateTime(text: string): string {
  const d = digits(text, 12);
  return [maskDate(d.slice(0, 8)), maskTime(d.slice(8))].filter(Boolean).join(" ");
}

function isoDate(text: string): string | null {
  const m = /^(\d{2})\.(\d{2})\.(\d{4})$/.exec(text);
  if (!m) return null;
  const [day, month, year] = [Number(m[1]), Number(m[2]), Number(m[3])];
  const d = new Date(year, month - 1, day);
  if (d.getFullYear() !== year || d.getMonth() !== month - 1 || d.getDate() !== day) return null;
  return `${m[3]}-${m[2]}-${m[1]}`;
}

function isoTime(text: string): string | null {
  const m = /^(\d{2}):(\d{2})$/.exec(text);
  return m && Number(m[1]) < 24 && Number(m[2]) < 60 ? text : null;
}

function isoDateTime(text: string): string | null {
  const [day, time] = text.split(" ");
  const d = isoDate(day ?? "");
  const t = isoTime(time ?? "");
  return d && t ? `${d}T${t}` : null;
}

const showDate = (iso: string) => (/^\d{4}-\d{2}-\d{2}$/.test(iso) ? iso.split("-").reverse().join(".") : "");
const showTime = (iso: string) => (/^\d{2}:\d{2}/.test(iso) ? iso.slice(0, 5) : "");
const showDateTime = (iso: string) => {
  const [day, time] = iso.split("T");
  return showDate(day ?? "") && showTime(time ?? "") ? `${showDate(day ?? "")} ${showTime(time ?? "")}` : "";
};

interface Kind {
  mask: (text: string) => string;
  parse: (text: string) => string | null;
  show: (iso: string) => string;
  placeholder: string;
  length: number;
}

const DATE: Kind = { mask: maskDate, parse: isoDate, show: showDate, placeholder: "дд.мм.гггг", length: 10 };
const TIME: Kind = { mask: maskTime, parse: isoTime, show: showTime, placeholder: "чч:мм", length: 5 };
const DATE_TIME: Kind = { mask: maskDateTime, parse: isoDateTime, show: showDateTime, placeholder: "дд.мм.гггг чч:мм", length: 16 };

function Masked({ kind, value, onChange, min, max, className = "inp", ...rest }: Props & { kind: Kind }) {
  const [text, setText] = useState(() => kind.show(value));
  const inRange = (iso: string) => (!min || iso >= min) && (!max || iso <= max);
  const parsed = (t: string) => {
    const iso = kind.parse(t);
    return iso && inRange(iso) ? iso : "";
  };

  // Follow the value only when it changes from outside (a reset, a loaded draft), not while typing.
  const [shown, setShown] = useState(value);
  if (value !== shown) {
    setShown(value);
    if (parsed(text) !== value) setText(kind.show(value));
  }

  const invalid = text.length === kind.length && !parsed(text);
  return (
    <input
      {...rest}
      className={className}
      type="text"
      inputMode="numeric"
      autoComplete="off"
      placeholder={kind.placeholder}
      maxLength={kind.length}
      value={text}
      aria-invalid={rest["aria-invalid"] || invalid || undefined}
      onChange={(event) => {
        const next = kind.mask(event.target.value);
        setText(next);
        const iso = parsed(next);
        if (iso !== value) onChange(iso);
      }}
    />
  );
}

export function DateInput(props: Props) {
  return <Masked kind={DATE} {...props} />;
}

export function TimeInput(props: Props) {
  return <Masked kind={TIME} {...props} />;
}

export function DateTimeInput(props: Props) {
  return <Masked kind={DATE_TIME} {...props} />;
}
