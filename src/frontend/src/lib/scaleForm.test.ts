import { describe, expect, it } from "vitest";
import type { ScaleForm } from "../api/types";
import { validScaleAnswer, visibleScaleItems } from "./scaleForm";
import { buildResponsePayload, isComplete } from "../screens/patient/questionnaireFlow";

const form: ScaleForm = {
  code: "T", name: "Conditional interview", version: 1, filled_by: "patient_clinician",
  items: [
    { id: "attempt", text: "Attempt", options: [{ value: 0, label: "No" }, { value: 1, label: "Yes" }] },
    { id: "count", text: "Count", input: "number", min: 1, max: 100, options: [], show_if: { conditions: [{ item: "attempt", equals: 1 }] } },
    { id: "detail", text: "Detail", options: [{ value: 0, label: "No" }], show_if: { conditions: [{ item: "count", equals: 1 }] } },
  ],
};

describe("extended scale forms", () => {
  it("requires only visible questions and removes stale hidden answers from payloads", () => {
    const answers = { attempt: 0, count: 1, detail: 0 };
    expect(visibleScaleItems(form, answers).map((i) => i.id)).toEqual(["attempt"]);
    expect(isComplete(form, answers)).toBe(true);
    expect(buildResponsePayload(form, answers, "s", null).answers).toEqual({ attempt: 0 });
    expect(isComplete(form, { attempt: 1 })).toBe(false);
    expect(isComplete(form, { attempt: 1, count: 2 })).toBe(true);
    expect(isComplete(form, { attempt: 1, count: 1 })).toBe(false);
    expect(isComplete(form, { attempt: 1, count: 1, detail: 0 })).toBe(true);
  });

  it("rejects empty, fractional and out-of-range numeric answers", () => {
    const item = form.items[1];
    if (!item) throw new Error("Missing test item");
    expect(validScaleAnswer(item, undefined)).toBe(false);
    expect(validScaleAnswer(item, 0)).toBe(false);
    expect(validScaleAnswer(item, 1.5)).toBe(false);
    expect(validScaleAnswer(item, 101)).toBe(false);
    expect(validScaleAnswer(item, 100)).toBe(true);
  });
});
