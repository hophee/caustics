import type { ScaleForm } from "../api/types";

export function validScaleAnswer(item: ScaleForm["items"][number], value: number | undefined): boolean {
  if (value === undefined || !Number.isInteger(value)) return false;
  return item.input === "number"
    ? value >= (item.min ?? 0) && (item.max == null || value <= item.max)
    : item.options.some((option) => option.value === value);
}

export function visibleScaleItems(form: ScaleForm, answers: Record<string, number>): ScaleForm["items"] {
  const active: Record<string, number> = {};
  return form.items.filter((item) => {
    if (item.show_if) {
      const matches = item.show_if.conditions.map((c) => active[c.item] === c.equals);
      if (!(item.show_if.match === "any" ? matches.some(Boolean) : matches.every(Boolean))) return false;
    }
    const answer = answers[item.id];
    if (answer !== undefined && validScaleAnswer(item, answer)) active[item.id] = answer;
    return true;
  });
}
