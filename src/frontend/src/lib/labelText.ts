/**
 * ОХЛП text as the parser left it (PDF → Markdown): Symbol-font bullets in the private-use area, control
 * characters of broken font encodings, list and heading marks, «[ ]» checkboxes, table pipes and rules,
 * code fences, items joined with «.;».
 * `labelText` keeps the words and drops the markup; `clip` shortens at a word, never inside one.
 */

// eslint-disable-next-line no-control-regex -- control characters are what this strips
const GLYPHS = /[\u0000-\u0008\u000B-\u001F\u007F\uE000-\uF8FF\u00AD\u200B-\u200F\u2060\uFEFF\uFFFD\u25A0-\u25A1\u25AA\u25AB\u25CF\u25E6]/g;
const TABLE_RULE = /\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?/g;

function cleanCell(raw: string): string {
  return raw
    .replace(/^\s*#{1,6}(?:\s+|$)/, "")
    .replace(/^\s*(?:[-*•·–—]\s+)+/, "")
    .replace(/\[[ xX]?\]\s*/g, "")
    .replace(/\*\*(\S(?:[^*]*?\S)?)\*\*/g, "$1")
    .replace(/\^\*/g, "*")
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([.,;:)»])/g, "$1")
    .replace(/([(«])\s+/g, "$1")
    .replace(/\.{2}(?!\.)/g, ".")
    .trim();
}

/** A table row becomes its cells joined with « · »; other lines lose their list and heading marks. */
function cleanLine(raw: string): string {
  return raw
    .replace(TABLE_RULE, " ")
    .split("|")
    .map(cleanCell)
    .filter(Boolean)
    .join(" · ");
}

export function labelText(raw: string): string {
  const text = raw
    .replace(GLYPHS, "")
    .replace(/\u00A0/g, " ")
    .replace(/\\/g, "/")
    .replace(/^\s*МКБ-10: нет данных;\s*/, "")
    // A page break inside a sentence: «клинической; ## реакции».
    .replace(/;\s*#{1,6}\s+/g, " ")
    // Items the parser joined into one value: «Шизофрения.; Мании…» → one per line.
    .replace(/([.:|])\s*;\s*/g, "$1\n")
    .replace(/^\s*`{3,}.*$/gm, "");
  return text
    .split("\n")
    .map(cleanLine)
    .filter((line) => line && !/^[.,;:·]+$/.test(line))
    .join("\n");
}

/** At most `limit` characters, cut after a sentence or at a space, with «…». */
export function clip(text: string, limit: number): string {
  if (text.length <= limit) return text;
  const head = text.slice(0, limit + 1);
  const sentence = Math.max(head.lastIndexOf(". "), head.lastIndexOf("\n"));
  const space = head.search(/\s\S*$/);
  const cut = sentence >= limit * 0.6 ? sentence + 1 : space > 0 ? space : limit;
  return `${text.slice(0, cut).replace(/[\s.,;:–—-]+$/, "")}…`;
}
