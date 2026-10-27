import { describe, expect, it } from "vitest";
import { criterionFor, evaluateCourse } from "./efficacy";
import { completionErrors, emptyEncounter, nextAfterTests } from "./model";
describe("encounter decisions", () => {
  it("routes repeat dose and unchanged treatment around diagnostic reassessment", () => {
    expect(nextAfterTests("first")).toBe("diagnosis");
    expect(nextAfterTests("repeat", "diagnosis")).toBe("diagnosis");
    expect(nextAfterTests("repeat", "dose")).toBe("decision");
  });
  it("does not require an examination, but refuses to complete an unconfirmed change", () => {
    const visit = {...emptyEncounter(), diagnosis: "F41.1", diagnosisSaved: true, risk: "low" as const, action: "continue" as const};
    expect(completionErrors(visit)).toEqual([]);
    expect(completionErrors({...visit, action: "dose"})).toContain("Подтвердите обновлённую аптечку.");
  });
});
describe("efficacy without invented results", () => {
  const rule = criterionFor("F41.1");
  if (!rule) throw new Error("HARS criterion missing");
  it("reminds for a missing measurement instead of calling treatment ineffective", () => {
    const result = evaluateCourse(rule, "2026-09-01", "2026-10-01", [{day: "2026-09-01", value: 24}, {day: "2026-09-10", value: 22}]);
    expect(result.kind).toBe("missing");
  });
  it("does not manufacture baseline or divide by zero", () => {
    expect(evaluateCourse(rule, "2026-09-01", "2026-10-01", [{day: "2026-10-01", value: 12}]).kind).toBe("unknown");
    expect(evaluateCourse(rule, "2026-09-01", "2026-10-01", [{day: "2026-09-01", value: 0}, {day: "2026-10-01", value: 12}]).kind).toBe("unknown");
  });
  it("preserves the strict >50% HARS criterion and never diagnoses resistance", () => {
    const points = [{day: "2026-09-01", value: 24}, {day: "2026-10-01", value: 12}];
    expect(evaluateCourse(rule, "2026-09-01", "2026-10-01", points).kind).toBe("review");
    expect(evaluateCourse(rule, "2026-09-01", "2026-10-01", [...points.slice(0,1), {day: "2026-10-01", value: 11}]).kind).toBe("dynamic");
    expect(criterionFor("F32.1")?.text).toContain("Один курс не устанавливает резистентность");
  });
  it("leaves scales without a KR threshold as dynamics", () => {
    const cgi = criterionFor("F40.1");
    if (!cgi) throw new Error("CGI criterion missing");
    const result = evaluateCourse(cgi, "2026-09-01", "2026-10-01", [{day: "2026-09-01", value: 5}, {day: "2026-10-01", value: 4}]);
    expect(result.kind).toBe("dynamic");
  });
});
