/**
 * The draft lives in the doctor's browser (D-040). It is kept in sessionStorage per patient, so it
 * survives moving between the visit workspace, the card and the draft page within the tab session.
 * Every component using the same key sees the same value (one in-memory cache with subscribers,
 * mirrored to sessionStorage).
 */
import { useCallback, useSyncExternalStore } from "react";
import { EMPTY_DRAFT, type DraftState } from "./model";

const cache = new Map<string, unknown>();
const listeners = new Map<string, Set<() => void>>();

function read<T>(key: string, fallback: T, valid: (v: unknown) => v is T): T {
  if (cache.has(key)) return cache.get(key) as T;
  let value = fallback;
  try {
    const raw = window.sessionStorage.getItem(key);
    if (raw) {
      const parsed: unknown = JSON.parse(raw);
      if (valid(parsed)) value = parsed;
    }
  } catch {
    // storage unavailable or corrupt: start empty
  }
  cache.set(key, value);
  return value;
}

function write(key: string, value: unknown, fallback: unknown): void {
  cache.set(key, value);
  try {
    if (value === fallback || value === null) window.sessionStorage.removeItem(key);
    else window.sessionStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage unavailable: the value lives in memory only
  }
  listeners.get(key)?.forEach((notify) => notify());
}

export function useStored<T>(key: string, fallback: T, valid: (v: unknown) => v is T): [T, (next: T | ((prev: T) => T)) => void] {
  const subscribe = useCallback(
    (notify: () => void) => {
      const set = listeners.get(key) ?? new Set();
      set.add(notify);
      listeners.set(key, set);
      return () => set.delete(notify);
    },
    [key],
  );
  const value = useSyncExternalStore(subscribe, () => read(key, fallback, valid));
  const set = useCallback(
    (next: T | ((prev: T) => T)) => {
      const prev = read(key, fallback, valid);
      write(key, typeof next === "function" ? (next as (p: T) => T)(prev) : next, fallback);
    },
    [key, fallback, valid],
  );
  return [value, set];
}

const isDraft = (v: unknown): v is DraftState =>
  typeof v === "object" && v !== null && Array.isArray((v as DraftState).items) && Array.isArray((v as DraftState).stopped);

export function useDraftState(patientId: string) {
  return useStored<DraftState>(`kompas.draft.${patientId}`, EMPTY_DRAFT, isDraft);
}
