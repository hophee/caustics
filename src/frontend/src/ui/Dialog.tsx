/**
 * Modal dialog. Renders the board's scrim as a fixed overlay `<div class="dlg scr-<name>">`, so the
 * dialog's generated stylesheet (scoped `.dlg.scr-<name>`) applies. Escape and a click on the scrim
 * close it; focus moves into the dialog and returns on close.
 */
import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  name: string;
  onClose: () => void;
  labelledBy: string;
  /** Extra class on the scrim (e.g. the board's root class `sc` / `scrim`). */
  className?: string;
  children: ReactNode;
}

export function Dialog({ name, onClose, labelledBy, className, children }: Props) {
  const ref = useRef<HTMLDivElement>(null);
  // Callers pass inline handlers; keep the latest one without re-running the open/close effect
  // (re-running it would move focus while the user types).
  const closeRef = useRef(onClose);
  useEffect(() => {
    closeRef.current = onClose;
  });

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    // first field before any button (the close button comes first in the markup)
    const target =
      ref.current?.querySelector<HTMLElement>("[autofocus], input, textarea, select") ??
      ref.current?.querySelector<HTMLElement>("button");
    target?.focus();
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") closeRef.current();
    };
    document.addEventListener("keydown", onKey);
    document.body.style.overflow = "hidden";
    return () => {
      document.removeEventListener("keydown", onKey);
      document.body.style.overflow = "";
      previous?.focus?.();
    };
  }, []);

  return (
    <div
      className={`dlg scr-${name}${className ? ` ${className}` : ""}`}
      onMouseDown={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div ref={ref} className="modal" role="dialog" aria-modal="true" aria-labelledby={labelledBy}>
        {children}
      </div>
    </div>
  );
}

export function CloseButton({ onClick }: { onClick: () => void }) {
  return (
    <button className="btn btn-t x" type="button" aria-label="Закрыть" style={{ width: 36, padding: 0 }} onClick={onClick}>
      <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
        <path d="M6 6l12 12M18 6L6 18" />
      </svg>
    </button>
  );
}
