/** Inline icons copied from the canvas boards. */

export function CompassMark({ size = 24, stroke = "#16161a", width = 1.6 }: { size?: number; stroke?: string; width?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 32 32" fill="none" stroke={stroke} strokeWidth={width} aria-hidden="true">
      <circle cx="16" cy="16" r="13" />
      <path d="M16 5v3M16 24v3M5 16h3M24 16h3" />
      <path d="M19.5 12.5L14 14l-1.5 5.5L18 18z" fill={stroke} />
    </svg>
  );
}

export function SearchIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="#5f5d58" strokeWidth="1.8" aria-hidden="true">
      <circle cx="11" cy="11" r="7" />
      <path d="M20 20l-4-4" />
    </svg>
  );
}

export function MenuIcon() {
  return (
    <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8" aria-hidden="true">
      <path d="M4 7h16M4 12h16M4 17h16" />
    </svg>
  );
}

/** Placeholder portrait when no photo is uploaded. */
export function Silhouette({ size = 72 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 72 72" aria-hidden="true">
      <circle cx="36" cy="30" r="14" fill="#c9c5bc" />
      <path d="M6 74c3-17 15-25 30-25s27 8 30 25z" fill="#c9c5bc" />
    </svg>
  );
}

export function PillIcon() {
  return (
    <svg width="22" height="22" viewBox="0 0 24 24" fill="none" stroke="#16161a" strokeWidth="1.7" aria-hidden="true">
      <rect x="3" y="9" width="18" height="7" rx="3.5" transform="rotate(-35 12 12.5)" />
      <path d="M9.5 8l5 8" />
    </svg>
  );
}
