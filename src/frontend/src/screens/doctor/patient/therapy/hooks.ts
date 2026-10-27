import { useEffect, useState } from "react";

/** `value`, updated `ms` after it stops changing (structural equality via JSON). */
export function useDebounced<T>(value: T, ms: number): T {
  const [state, setState] = useState(value);
  const serial = JSON.stringify(value);
  useEffect(() => {
    const timer = window.setTimeout(() => setState(JSON.parse(serial) as T), ms);
    return () => window.clearTimeout(timer);
  }, [serial, ms]);
  return state;
}
