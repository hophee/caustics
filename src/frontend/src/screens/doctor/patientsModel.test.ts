import { describe, expect, it } from "vitest";
import type { PatientSummary } from "../../api/types";
import { activityAgo, daySquare, filterPatients, matchesQuery, patientStatus, squares, statusCounts } from "./patientsModel";

const now = new Date(2026, 9, 25, 12, 0);

function patient(over: Partial<PatientSummary>): PatientSummary {
  return {
    patient_id: "p",
    full_name: "Анна Смирнова",
    birth_date: "1992-03-01",
    sex: "female",
    has_account: true,
    case_id: "c",
    case_status: "active",
    main_icd: "F33.2",
    last_entry_at: new Date(2026, 9, 25, 8, 14).toISOString(),
    last_visit_at: null,
    last_activity_at: null,
    days: [
      { day: "2026-10-23", level: "stable", pending: false },
      { day: "2026-10-24", level: null, pending: false },
      { day: "2026-10-25", level: "attention", pending: false },
    ],
    open_alerts: 0,
    top_alert_level: null,
    therapy: [],
    ...over,
  };
}

describe("diary squares", () => {
  it("maps levels, pending entries and empty days", () => {
    expect(daySquare({ level: "stable", pending: false })).toBe("s");
    expect(daySquare({ level: "attention", pending: false })).toBe("a");
    expect(daySquare({ level: "urgent", pending: false })).toBe("u");
    expect(daySquare({ level: null, pending: true })).toBe("w");
    expect(daySquare({ level: null, pending: false })).toBe("n");
  });

  it("takes the last N days", () => {
    expect(squares(patient({}).days, 2)).toEqual(["n", "a"]);
  });
});

describe("time since patient activity", () => {
  it.each([
    [0, "1 мин. назад"],
    [60_001, "2 мин. назад"],
    [3_600_000, "1 ч. назад"],
    [3_600_001, "2 ч. назад"],
    [86_400_000, "1 день назад"],
    [86_400_001, "2 дня назад"],
    [5 * 86_400_000, "5 дней назад"],
    [21 * 86_400_000, "21 день назад"],
    [-60_000, "1 мин. назад"],
  ])("rounds elapsed %i ms upward: %s", (elapsed, expected) => {
    expect(activityAgo(new Date(now.getTime() - elapsed).toISOString(), now)).toBe(expected);
  });
});

describe("patient status", () => {
  it("prefers the top open alert", () => {
    expect(patientStatus(patient({ open_alerts: 2, top_alert_level: "urgent" }), now)).toBe("urgent");
  });

  it("is «none» when the diary is stale or empty", () => {
    expect(patientStatus(patient({ last_entry_at: new Date(2026, 9, 19, 20, 0).toISOString() }), now)).toBe("none");
    expect(patientStatus(patient({ last_entry_at: null, days: [] }), now)).toBe("none");
  });

  it("uses the latest assessed diary day otherwise", () => {
    expect(patientStatus(patient({}), now)).toBe("attention");
  });

  it("counts statuses in a fixed order", () => {
    const list = [patient({ open_alerts: 1, top_alert_level: "high" }), patient({}), patient({ last_entry_at: null })];
    expect(statusCounts(list, now)).toEqual([
      { key: "high", count: 1 },
      { key: "attention", count: 1 },
      { key: "none", count: 1 },
    ]);
  });
});

describe("search", () => {
  it("matches names in any order, ignoring case and ё", () => {
    const p = patient({ full_name: "Пётр Ковалёв", main_icd: "F41.1" });
    expect(matchesQuery(p, "ковалев")).toBe(true);
    expect(matchesQuery(p, "ковалёв пётр")).toBe(true);
    expect(matchesQuery(p, "анна")).toBe(false);
  });

  it("matches ICD code prefixes", () => {
    const p = patient({ main_icd: "F33.2" });
    expect(matchesQuery(p, "f33")).toBe(true);
    expect(matchesQuery(p, "F33.2")).toBe(true);
    expect(matchesQuery(p, "F41")).toBe(false);
  });

  it("filters by query and status", () => {
    const list = [patient({ patient_id: "1" }), patient({ patient_id: "2", full_name: "Игорь Белов", main_icd: "F41.1", open_alerts: 1, top_alert_level: "high" })];
    expect(filterPatients(list, "", "high", now).map((p) => p.patient_id)).toEqual(["2"]);
    expect(filterPatients(list, "F33", null, now).map((p) => p.patient_id)).toEqual(["1"]);
  });
});
