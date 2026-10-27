import { describe, expect, it } from "vitest";
import { guidelineLabel, standardLabel } from "./diagnosesModel";

describe("diagnosis source labels", () => {
  it("names the КР with its year", () => {
    expect(guidelineLabel({ id: "457_3", title: "Генерализованное тревожное расстройство", year: 2024, url: "" })).toBe(
      "Генерализованное тревожное расстройство · 2024",
    );
  });

  it("names the standard by its order", () => {
    expect(standardLabel({ number: "745н", date: "2026-08-24", title: "", url: "" })).toBe("Приказ 745н от 24.08.2026");
  });
});
