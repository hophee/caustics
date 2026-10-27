/** Guideline selection (spec §6) as view rows: КР rows, МНН groups and candidate lookup. */
import type { Candidate, KrRow, Selection } from "../../../../api/types";

export interface MnnGroup {
  mnn: string;
  candidates: Candidate[];
}

export interface RowGroup {
  row: KrRow;
  title: string;
  groups: MnnGroup[];
}

const lower = (s: string) => s.charAt(0).toLowerCase() + s.slice(1);

export function rowTitle(row: KrRow): string {
  return [row.window, row.scheme ? lower(row.scheme) : ""].filter(Boolean).join(", ");
}

/** «КР 301_3 (F32:F33 .pdf), п. 3.1.3» → «КР 301_3, п. 3.1.3» */
export function shortSource(source: string): string {
  return source.replace(/\s*\([^)]*\)/g, "").trim();
}

function substanceKey(c: Candidate): string {
  return c.label.substances.join(" + ");
}

/** Same window, scheme and condition: one group even if КР lists it at several stages (D-122). */
const rowKey = (row: KrRow) => [row.window, row.scheme, row.condition, row.stage].join("|");

/**
 * Rows in guideline order, rows repeated across stages merged; МНН in the order the first row names
 * them, then the rest alphabetically.
 */
export function groupCandidates(selection: Selection | undefined): RowGroup[] {
  if (!selection) return [];
  const merged = new Map<string, KrRow[]>();
  for (const row of selection.rows) merged.set(rowKey(row), [...(merged.get(rowKey(row)) ?? []), row]);
  return [...merged.values()].map((rows) => {
    const row = rows[0] as KrRow;
    const ids = new Set(rows.map((r) => r.id));
    const byMnn = new Map<string, Candidate[]>();
    for (const c of selection.candidates) {
      if (!ids.has(c.row.id)) continue;
      const key = substanceKey(c);
      const list = byMnn.get(key) ?? [];
      if (!list.some((x) => x.label.id === c.label.id)) list.push(c);
      byMnn.set(key, list);
    }
    const order = row.targets.map((t) => t.mnn).filter((m): m is string => Boolean(m));
    const rank = (m: string) => {
      const i = order.indexOf(m);
      return i < 0 ? order.length : i;
    };
    const groups = [...byMnn.entries()]
      .map(([mnn, candidates]) => ({ mnn, candidates }))
      .sort((a, b) => rank(a.mnn) - rank(b.mnn) || a.mnn.localeCompare(b.mnn, "ru"));
    return { row, title: rowTitle(row), groups };
  });
}

/** label id → the first candidate (row) suggesting it. */
export function candidateIndex(selection: Selection | undefined): Map<string, Candidate> {
  const index = new Map<string, Candidate>();
  for (const c of selection?.candidates ?? []) if (!index.has(c.label.id)) index.set(c.label.id, c);
  return index;
}

/** Case-insensitive match on trade name, МНН or АТХ code. */
export function matches(q: string, fields: { trade_name: string; substances: string[]; atc: string | null }): boolean {
  const needle = q.trim().toLowerCase();
  if (!needle) return true;
  return [fields.trade_name, ...fields.substances, fields.atc ?? ""].some((v) => v.toLowerCase().includes(needle));
}

/** Splits `text` around the first case-insensitive occurrence of `q` for <mark>. */
export function highlight(text: string, q: string): [string, string, string] {
  const needle = q.trim().toLowerCase();
  const i = needle ? text.toLowerCase().indexOf(needle) : -1;
  if (i < 0) return [text, "", ""];
  return [text.slice(0, i), text.slice(i, i + needle.length), text.slice(i + needle.length)];
}
