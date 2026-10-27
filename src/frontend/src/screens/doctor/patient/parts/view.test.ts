import { describe, expect, it } from "vitest";
import type { Alert } from "../../../../api/types";
import {
  alertTitle,
  alertTrack,
  caseText,
  comorbid,
  listText,
  mainCodes,
  patientAlerts,
  pregnancyChip,
  showsPregnancy,
  therapyItem,
  therapyLine,
  therapyTitle,
  weightText,
} from "./view";

describe("profile facts", () => {
  it("null list is unknown, empty list is an explicit none", () => {
    expect(listText(null)).toBe("Неизвестно");
    expect(listText([])).toBe("нет");
    expect(listText(["пароксетин", "лактоза"])).toBe("пароксетин, лактоза");
  });

  it("hides pregnancy for men only", () => {
    expect(showsPregnancy("male")).toBe(false);
    expect(showsPregnancy("female")).toBe(true);
    expect(showsPregnancy(null)).toBe(true);
  });

  it("pregnancy chip: positive first, then unknown", () => {
    expect(pregnancyChip({ pregnant: "no", lactating: "yes" })).toEqual({ text: "лактация", cls: "chip red" });
    expect(pregnancyChip({ pregnant: "unknown", lactating: "no" })).toEqual({ text: "неизвестно", cls: "chip nd" });
    expect(pregnancyChip({ pregnant: "no", lactating: "no" })).toBeNull();
  });

  it("comorbid diagnoses exclude the case's main one", () => {
    expect(comorbid(["F33.2", "I10"], mainCodes([{ icd_code: "F33.2", is_main: true }], null))).toEqual(["I10"]);
    expect(mainCodes([], "F41.1")).toEqual(["F41.1"]);
  });

  it("case, weight", () => {
    expect(caseText(null)).toBe("нет активного");
    expect(caseText({ status: "active", opened_at: "2026-10-01T10:00:00", closed_at: null })).toBe("активен с 01.10");
    expect(weightText(null)).toBeNull();
    expect(weightText(58.5)).toBe("58,5 кг");
  });
});

describe("current therapy", () => {
  it("maps the real therapy/current item", () => {
    const item = therapyItem({
      ref: "c1",
      label_id: "x",
      substance: "амитриптилин",
      regimen: { schedule: { intakes: [{ at: "21:00:00", amount_mg: 25.0 }], kind: "daily" }, start: "2026-10-23", duration_days: 28 },
      patient_reported: false,
    });
    expect(therapyTitle(item)).toBe("Амитриптилин 25 мг");
    expect(therapyLine(item)).toBe("в 21:00 · с 23.10, 28 дн.");
  });

  it("patient-reported drug known only by name", () => {
    const item = therapyItem({ ref: "reported:1", label_id: null, substance: "мелатонин", regimen: null, patient_reported: true });
    expect(therapyTitle(item)).toBe("Мелатонин");
    expect(therapyLine(item)).toBe("со слов пациента");
  });

  it("as-needed dose", () => {
    const item = therapyItem({ ref: "c2", substance: "алпразолам", regimen: { schedule: { kind: "prn", dose_mg: 0.5 }, start: "2026-10-01", duration_days: 14 } });
    expect(item.dose).toBe("0,5 мг по потребности");
  });
});

describe("alerts", () => {
  const base = { patient_id: "p", status: "sent", source: "scale:PHQ-9", details: { item: 9, value: 1 }, reason: "PHQ-9 · опросник: критический ответ — текст", accepted_at: null, handled_at: null } as unknown as Alert;

  it("filters by patient, most severe and newest first", () => {
    const alerts = [
      { ...base, alert_id: "1", level: "planned", created_at: "2026-10-25T10:00:00Z" },
      { ...base, alert_id: "2", level: "urgent", created_at: "2026-10-24T10:00:00Z" },
      { ...base, alert_id: "3", level: "urgent", created_at: "2026-10-25T09:00:00Z" },
      { ...base, alert_id: "4", level: "urgent", created_at: "2026-10-25T09:00:00Z", patient_id: "other" },
    ] as Alert[];
    expect(patientAlerts(alerts, "p").map((a) => a.alert_id)).toEqual(["3", "2", "1"]);
  });

  it("title from the scale item, track shows only the last interaction", () => {
    expect(alertTitle(base)).toBe("PHQ-9, пункт 9 = 1");
    expect(alertTitle({ ...base, source: "diary", details: {} })).toBe("PHQ-9 · опросник: критический ответ");
    expect(alertTrack({ ...base, created_at: "2026-10-24T15:00:00", accepted_at: "2026-10-24T15:48:00" })).toBe(
      "принят вами 24.10 в 15:48 · решение не записано",
    );
    expect(alertTrack({ ...base, created_at: "2026-10-24T15:00:00" })).toBe("создан 24.10 в 15:00");
  });
});
