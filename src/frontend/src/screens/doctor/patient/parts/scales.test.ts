import { describe, expect, it } from "vitest";
import type { DoctorResult, ScaleForm } from "../../../../api/types";
import { assignmentNext, assignmentSchedule, criticalText, scaleMax, scaleRows, seriesPoints } from "./scales";

function r(scale: string, at: string, total: number | null, band?: string, critical = false): DoctorResult {
  return {
    response_id: `${scale}${at}`,
    scale_code: scale,
    scale_version: 1,
    taken_at: at,
    status: "complete",
    answers: {},
    problems: [],
    total,
    band: band ? { label: band } : null,
    critical_hits: critical ? [{ item: 9, value: 1 }] : [],
    subscores: [],
    explanation: [],
  } as DoctorResult;
}

describe("scale rows", () => {
  it("first, last and change per scale, never across scales", () => {
    const rows = scaleRows([
      r("PHQ-9", "2026-10-24T10:00:00", 15, "Умеренно тяжёлая"),
      r("PHQ-9", "2026-10-01T10:00:00", 19, "Умеренно тяжёлая"),
      r("HDRS-17", "2026-10-01T11:00:00", 21, "Тяжёлое"),
      r("GAD-7", "2026-10-02T11:00:00", null),
    ]);
    expect(rows).toEqual([
      { code: "HDRS-17", first: { value: 21, day: "2026-10-01" }, last: null, delta: null, band: "тяжёлое" },
      { code: "PHQ-9", first: { value: 19, day: "2026-10-01" }, last: { value: 15, day: "2026-10-24" }, delta: -4, band: "умеренно тяжёлая" },
    ]);
  });

  it("max of a form is the sum of the largest options", () => {
    const form = { items: [{ id: 1, options: [{ value: 0 }, { value: 4 }] }, { id: 2, options: [{ value: 0 }, { value: 2 }] }] } as ScaleForm;
    expect(scaleMax(form)).toBe(6);
    expect(scaleMax(undefined)).toBeNull();
  });

  it("critical answers and series points", () => {
    const res = r("PHQ-9", "2026-10-24T10:00:00", 15, undefined, true);
    expect(criticalText(res)).toEqual(["пункт 9 = 1"]);
    const points = seriesPoints({ scale_code: "PHQ-9", subscale_code: null, points: [{ taken_on: "2026-10-24", value: 15 }, { taken_on: "2026-10-01", value: 19 }] }, [res]);
    expect(points).toEqual([
      { day: "2026-10-24", value: 15, critical: true },
      { day: "2026-10-01", value: 19, critical: false },
    ]);
  });
});

describe("assignments", () => {
  it("repeat: next date on the schedule", () => {
    const a = { mode: "repeat", every_days: 14, starts_on: "2026-10-10", due_days: 3 };
    expect(assignmentSchedule(a)).toBe("каждые 14 дней с 10.10");
    expect(assignmentNext(a, "2026-10-25")).toBe("следующая 07.11");
    expect(assignmentNext(a, "2026-10-24")).toBe("следующая 24.10");
  });

  it("once: until the due date", () => {
    const a = { mode: "once", every_days: null, starts_on: "2026-10-24", due_days: 3 };
    expect(assignmentSchedule(a)).toBe("разово");
    expect(assignmentNext(a, "2026-10-25")).toBe("до 27.10");
    expect(assignmentNext(a, "2026-10-28")).toBe("срок истёк 27.10");
  });
});

it("uses the rule note when a critical hit has no question number", () => {
  const result = {
    ...r("C-SSRS", "2026-10-24T10:00:00", null),
    critical_hits: [
      { item: 2, value: 1 },
      { item: null, value: null, note: "Требуется оценка риска" },
      { item: null, value: null },
    ],
  } as DoctorResult;
  expect(criticalText(result)).toEqual(["пункт 2 = 1", "Требуется оценка риска", "Критический критерий"]);
});
