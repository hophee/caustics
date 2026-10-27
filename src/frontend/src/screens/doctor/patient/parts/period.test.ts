import { describe, expect, it } from "vitest";
import { addDays, column, coursePeriod, daysBetween, eventTicks, fittingPeriod, periodOf, weekTicks } from "./period";

describe("period", () => {
  it("keeps the latest days that fit and restores earlier days when widened", () => {
    const p = periodOf("2026-11-01", "2026-11-30");
    expect(fittingPeriod(p, 402, 122)).toEqual(periodOf("2026-11-21", "2026-11-30"));
    expect(fittingPeriod(p, 262, 122)).toEqual(periodOf("2026-11-26", "2026-11-30"));
    expect(fittingPeriod(p, 1000, 122)).toEqual(p);
    expect(fittingPeriod(p, 0, 122)).toEqual(periodOf("2026-11-30", "2026-11-30"));
  });

  it("adds days across months and counts them", () => {
    expect(addDays("2026-10-30", 3)).toBe("2026-11-02");
    expect(daysBetween("2026-10-01", "2026-10-25")).toBe(24);
    expect(periodOf("2026-10-01", "2026-10-03").days).toEqual(["2026-10-01", "2026-10-02", "2026-10-03"]);
  });

  it("shows at least 28 days for a fresh case", () => {
    const p = coursePeriod("2026-10-25", "2026-10-25");
    expect(p.days).toHaveLength(28);
    expect(p.start).toBe("2026-09-28");
  });

  it("starts at the case opening or an earlier visit, capped at 60 days", () => {
    expect(coursePeriod("2026-09-01", "2026-10-25").start).toBe("2026-09-01");
    expect(coursePeriod("2026-10-20", "2026-10-25", { min: 3, earliest: ["2026-10-11", null] }).start).toBe("2026-10-11");
    expect(coursePeriod("2026-01-01", "2026-10-25").days).toHaveLength(60);
  });

  it("finds columns and weekly ticks", () => {
    const p = periodOf("2026-10-01", "2026-10-25");
    expect(column(p, "2026-10-01")).toBe(0);
    expect(column(p, "2026-10-25T10:00:00Z")).toBe(24);
    expect(column(p, "2026-09-30")).toBeNull();
    expect(weekTicks(p).map((t) => t.col)).toEqual([1, 8, 15, 22]);
  });

  it("labels the November diary example every two days after the weekly gap", () => {
    const p = periodOf("2026-11-03", "2026-11-14");
    expect(eventTicks(p, ["2026-11-10", "2026-11-11", "2026-11-12", "2026-11-13"]))
      .toEqual([{ day: "2026-11-03", col: 1 }, { day: "2026-11-10", col: 8 }, { day: "2026-11-12", col: 10 }, { day: "2026-11-14", col: 12 }]);
  });

  it("uses weekly labels without in-range events and handles a single day", () => {
    const p = periodOf("2026-11-01", "2026-11-30");
    expect(eventTicks(p, ["2026-10-31", "2026-12-01"])).toEqual(weekTicks(p));
    expect(eventTicks(periodOf("2026-11-14", "2026-11-14"), ["2026-11-14", "2026-11-14"]))
      .toEqual([{ day: "2026-11-14", col: 1 }]);
  });

  it("returns to weekly spacing across seven empty days between event runs", () => {
    const p = periodOf("2026-11-01", "2026-11-30");
    expect(eventTicks(p, ["2026-11-20", "2026-11-10", "2026-11-10", "2026-11-12"]).map((tick) => tick.day))
      .toEqual(["2026-11-03", "2026-11-10", "2026-11-12", "2026-11-20", "2026-11-27"]);
  });
});
