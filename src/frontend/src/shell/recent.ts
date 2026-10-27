/** «Недавние пациенты» in the doctor's side bar: a per-browser convenience list (localStorage). */
import { useSyncExternalStore } from "react";

const KEY = "kompas.recent";
const LIMIT = 3;

export interface Recent {
  id: string;
  name: string;
}

let listeners: (() => void)[] = [];
let cache: Recent[] | null = null;

function read(): Recent[] {
  if (cache) return cache;
  try {
    cache = JSON.parse(localStorage.getItem(KEY) ?? "[]") as Recent[];
  } catch {
    cache = [];
  }
  return cache;
}

export function rememberPatient(entry: Recent): void {
  const next = [entry, ...read().filter((r) => r.id !== entry.id)].slice(0, LIMIT);
  if (JSON.stringify(next) === JSON.stringify(read())) return;
  cache = next;
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage blocked: the list lives for this page only.
  }
  for (const listener of listeners) listener();
}

export function useRecentPatients(): Recent[] {
  return useSyncExternalStore(
    (listener) => {
      listeners.push(listener);
      return () => {
        listeners = listeners.filter((l) => l !== listener);
      };
    },
    read,
    () => [],
  );
}
