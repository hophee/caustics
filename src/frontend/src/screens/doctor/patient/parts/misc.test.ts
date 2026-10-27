import { describe, expect, it } from "vitest";
import type { ScaleForm } from "../../../../api/types";
import { answeredCount, runningSum } from "./rating";
import { visitFields } from "./visits";

describe("visit record", () => {
  it("known fields in order, then the rest, empty ones skipped", () => {
    expect(visitFields({ plan: "Дневник", complaints: "Сон", anamnesis: "", extra: ["a", "b"] })).toEqual([
      { label: "Жалобы", value: "Сон" },
      { label: "План", value: "Дневник" },
      { label: "extra", value: "a, b" },
    ]);
  });
});

describe("rating", () => {
  const form = { items: [{ id: 1, options: [] }, { id: 2, options: [] }, { id: 3, options: [] }] } as unknown as ScaleForm;
  it("counts answered items and sums them", () => {
    expect(answeredCount(form, { "1": 2, "3": 0 })).toBe(2);
    expect(runningSum(form, { "1": 2, "3": 1 })).toBe(3);
  });
});
