import { describe, expect, it } from "vitest";
import type { Alert, DoctorResult } from "../../../../api/types";
import { diaryLane, scaleLane, signalLane, therapyLane } from "./course";
import { periodOf } from "./period";

const p = periodOf("2026-10-20", "2026-10-25");

function result(scale: string, at: string, total: number, critical = false): DoctorResult {
  return {
    response_id: `${scale}${at}`,
    scale_code: scale,
    scale_version: 1,
    taken_at: at,
    status: "complete",
    answers: {},
    problems: [],
    total,
    critical_hits: critical ? [{ item: 9, value: 1 }] : [],
    subscores: [],
    explanation: [],
  } as DoctorResult;
}

describe("course line", () => {
  it("diary: worst level per day, no entry, entry without triage", () => {
    const lane = diaryLane(p, [
      { day: "2026-10-20", triage: { level: "stable" } },
      { day: "2026-10-20", triage: { level: "attention" } },
      { day: "2026-10-22", triage: null },
      { day: "2026-10-25", triage: { level: "urgent" } },
      { day: "2026-09-01", triage: { level: "urgent" } },
    ]);
    expect(lane).toEqual(["a", "n", "x", "n", "n", "u"]);
  });

  it("scales: one point per day, never adding scales together", () => {
    const points = scaleLane(p, [result("PHQ-9", "2026-10-21T09:00:00", 19), result("HDRS-17", "2026-10-21T10:00:00", 21), result("PHQ-9", "2026-10-25T09:00:00", 15, true)]);
    expect(points).toEqual([
      { col: 1, day: "2026-10-21", label: "PHQ-9 19; HDRS-17 21", critical: false },
      { col: 5, day: "2026-10-25", label: "PHQ-9 15 · пункт 9 = 1", critical: true },
    ]);
  });

  it("therapy: courses until stopped or ended, gaps are «без препаратов»", () => {
    const segments = therapyLane(p, [
      { confirmed_at: "2026-10-22T10:00:00", items: [{ course_id: "c1", trade_name: "Амитриптилин", regimen: { start: "2026-10-22", duration_days: 28 } }], stopped_course_ids: [] },
      { confirmed_at: "2026-10-24T10:00:00", items: [], stopped_course_ids: ["c1"] },
    ]);
    expect(segments).toEqual([
      { kind: "obs", col: 1, span: 2, label: "без препаратов" },
      { kind: "rx", col: 3, span: 3, label: "Амитриптилин" },
      { kind: "obs", col: 6, span: 1, label: "без препаратов" },
    ]);
  });

  it("therapy: no prescriptions at all is one «без препаратов» segment", () => {
    expect(therapyLane(p, [])).toEqual([{ kind: "obs", col: 1, span: 6, label: "без препаратов" }]);
  });

  it("signals: open alerts inside the period", () => {
    const alert = { alert_id: "a", level: "urgent", created_at: "2026-10-24T12:00:00" } as Alert;
    expect(signalLane(p, [alert])).toEqual([{ col: 5, cls: "lv-red", label: "Срочно 24.10", id: "a" }]);
    const high = { alert_id: "b", level: "high", created_at: "2026-10-24T09:00:00" } as Alert;
    expect(signalLane(p, [high, alert])).toEqual([{ col: 5, cls: "lv-red", label: "Срочно 24.10 ×2", id: "a" }]);
  });
});

it("shows rule-based results without null items or repeated identical labels", () => {
  const cssrs = {
    ...result("C-SSRS", "2026-10-21T09:00:00", 0),
    total: null,
    band: { label: "Высокий риск" },
    critical_hits: [{ item: null, value: null, note: "Требуется оценка риска" }],
  } as DoctorResult;
  expect(scaleLane(p, [cssrs, { ...cssrs, response_id: "second" }])).toEqual([
    { col: 1, day: "2026-10-21", label: "C-SSRS высокий риск · Требуется оценка риска", critical: true },
  ]);
});
