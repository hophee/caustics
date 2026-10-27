/** A mood face from the boards (I2-Today, P-Diary step 1). */
export function MoodFace({ mouth }: { mouth: string }) {
  return (
    <svg width="30" height="30" viewBox="0 0 28 28" fill="none" stroke="#3d3b37" strokeWidth="1.6" aria-hidden="true">
      <circle cx="14" cy="14" r="11" />
      <circle cx="10" cy="12" r=".8" fill="#3d3b37" />
      <circle cx="18" cy="12" r=".8" fill="#3d3b37" />
      <path d={mouth} />
    </svg>
  );
}

export function CloseIcon() {
  return (
    <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true">
      <path d="M6 6l12 12M18 6L6 18" />
    </svg>
  );
}

export function DoneIcon() {
  return (
    <svg width="26" height="26" viewBox="0 0 24 24" fill="none" stroke="#16161a" strokeWidth="2" aria-hidden="true">
      <path d="M5 12l5 5 9-10" />
    </svg>
  );
}
