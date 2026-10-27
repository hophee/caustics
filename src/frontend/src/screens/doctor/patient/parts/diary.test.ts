import { describe, expect, it } from "vitest";
import type { DoctorDiaryEntry } from "../../../../api/types";
import { lines } from "./chart";
import { latestValue, matches, metricPoints, metricsLine, sleepBars, triageSource } from "./diary";
import { periodOf } from "./period";

function entry(day: string, extra: Partial<DoctorDiaryEntry> = {}): DoctorDiaryEntry {
  return {
    entry_id: day,
    revision_id: day,
    revision: 1,
    schema_version: 1,
    day,
    mood: 4,
    anxiety: 7,
    energy: 3,
    sleep: "poor",
    complaints: [],
    side_effects: [],
    self_harm_thoughts: null,
    text: null,
    saved_at: `${day}T08:00:00Z`,
    triage: null,
    notes: [],
    ...extra,
  } as DoctorDiaryEntry;
}

describe("diary entries", () => {
  it("shows new diary scales without interpreting them as legacy scores", () => {
    expect(metricsLine(entry("2026-10-27", { schema_version: 2, mood: 0, anxiety: 3,
      irritability: 0, sleep_hours: 6.5, unusual_experiences: false }))).toBe(
      "настроение: депрессивное · тревога 3/3 · раздражительность 0/3 · сон 06:30 · необычные проявления: нет",
    );
  });
  it("metric line shows only answered fields", () => {
    expect(metricsLine(entry("2026-10-25"))).toBe("настроение 4 · тревога 7 · сон плохо");
    expect(
      metricsLine(entry("2026-10-25", { complaints: ["fatigue"], self_harm_thoughts: false, mood: null }), (c) => (c === "fatigue" ? "Упадок сил" : c), true),
    ).toBe("тревога 7 · энергия 3 · сон плохо · жалобы: упадок сил · мысли о самоповреждении: нет");
  });

  it("chart points per metric skip unanswered days; lines break on gaps", () => {
    const entries = [entry("2026-10-20", { mood: 6 }), entry("2026-10-21", { mood: null }), entry("2026-10-22", { mood: 3 }), entry("2026-10-23", { mood: 4 })];
    expect(metricPoints(entries, "mood").map((p) => p.value)).toEqual([6, 3, 4]);
    expect(latestValue(entries, "mood")).toBe(4);
    const p = periodOf("2026-10-20", "2026-10-23");
    const box = { width: 100, height: 100, left: 0, right: 0, top: 0, bottom: 0 };
    const parts = lines(p, metricPoints(entries, "mood"), 0, 10, box, true);
    expect(parts.map((part) => part.map((x) => x.value))).toEqual([[6], [3, 4]]);
    expect(parts[1]?.[0]?.y).toBe(70);
  });

  it("sleep bars with missing days", () => {
    const p = periodOf("2026-10-20", "2026-10-22");
    expect(sleepBars(p, [entry("2026-10-20", { sleep: "good" }), entry("2026-10-22", { sleep: null })])).toEqual(["sl-g", "x", "x"]);
  });

  it("filters", () => {
    const e = entry("2026-10-25", { text: "Спала плохо", triage: { level: "attention" } as DoctorDiaryEntry["triage"] });
    expect(matches(e, "text")).toBe(true);
    expect(matches(e, "attention")).toBe(true);
    expect(matches(e, "notes")).toBe(false);
    expect(matches(entry("2026-10-25", { text: "  " }), "text")).toBe(false);
  });

  it("triage source: what set the level", () => {
    const t = (reasons: object[], model_name: string | null = null) =>
      ({ level: "attention", reasons, model_name, model_unsure: false }) as unknown as DoctorDiaryEntry["triage"];
    expect(triageSource(t([{ source: "model", level: "attention" }], "Qwen3-4B"))).toBe("Qwen3-4B");
    expect(triageSource(t([{ source: "model", level: "stable" }, { source: "trend", level: "attention" }], "keywords"))).toBe("динамика");
    expect(triageSource(t([{ source: "rules", level: "attention" }]))).toBe("правила");
    expect(triageSource(null)).toBeNull();
  });
});
