import { describe, expect, it } from "vitest";
import { factRows, formatValue, groupBySubstance, maxDaily } from "./drugFacts";
import { scaleTitle } from "./scalesModel";

const label = {
  id: "x",
  indications: { value: "Депрессия", quote: "показан для лечения", section: "4.1" },
  min_age: { value: 6, quote: "с 6 лет", section: "4.1" },
  pregnancy: { value: "caution", quote: "при беременности", section: "4.6" },
  hepatic: { value: "contraindicated", quote: "тяжёлая печёночная", section: "4.2" },
  renal: { value: "no_data", quote: null, section: null },
  excipients: { value: [], quote: null, section: null },
  named_interactions: { value: ["литий", "селегилин"], quote: "  ИМАО  ", section: "4.5" },
  dose: {
    start_text: { value: "50 мг/сут", quote: "начинать с 50 мг", section: "4.2" },
    max_text: { value: null, quote: null, section: null },
    frequency_text: { value: "1 раз в сутки", quote: "один раз", section: "4.2" },
    max_daily_mg: 200,
  },
  new_fact: { value: "да", quote: "q", section: "5.1" },
};

describe("ОХЛП facts", () => {
  const rows = factRows(label);
  const row = (key: string) => rows.find((r) => r.key === key);

  it("keeps the section and quote behind every value", () => {
    expect(row("indications")).toMatchObject({ section: "4.1", value: "Депрессия", quote: "показан для лечения", missing: false });
    expect(row("named_interactions")).toMatchObject({ value: "литий, селегилин", quote: "ИМАО" });
    expect(row("dose.start_text")).toMatchObject({ section: "4.2", value: "50 мг/сут" });
  });

  it("translates status values and colours risky ones", () => {
    expect(row("pregnancy")).toMatchObject({ value: "с осторожностью", level: "amb" });
    expect(row("hepatic")).toMatchObject({ value: "противопоказано", level: "red" });
    expect(row("min_age")?.value).toBe("с 6 лет");
  });

  it("says «нет данных» for empty, null, no_data and absent facts — never a made-up value", () => {
    for (const key of ["renal", "excipients", "dose.max_text", "driving"]) {
      expect(row(key)).toMatchObject({ value: "нет данных", missing: true });
    }
  });

  it("appends fact-shaped fields it doesn't know", () => {
    expect(rows.at(-1)).toMatchObject({ key: "new_fact", section: "5.1", value: "да" });
  });

  it("limits rows to the preview keys", () => {
    expect(factRows(label, ["excipients", "indications"]).map((r) => r.key)).toEqual(["indications", "excipients"]);
  });

  it("formats values", () => {
    expect(formatValue("  ")).toBeNull();
    expect(formatValue(["", " а "])).toBe("а");
    expect(maxDaily(label)).toBe("200 мг/сут");
    expect(maxDaily({ dose: { max_daily_mg: null } })).toBeNull();
  });

  it("groups search results by substance", () => {
    const groups = groupBySubstance([
      { substances: ["сертралин"], atc: "N06AB06", id: 1 },
      { substances: ["сертралин"], atc: "N06AB06", id: 2 },
      { substances: ["агомелатин"], atc: null, id: 3 },
    ]);
    expect(groups.map((g) => [g.name, g.atc, g.items.length])).toEqual([
      ["сертралин", "N06AB06", 2],
      ["агомелатин", null, 1],
    ]);
  });
});

describe("scales", () => {
  it("strips the code from the scale name", () => {
    expect(scaleTitle("PHQ-9 · опросник здоровья пациента", "PHQ-9")).toBe("опросник здоровья пациента");
    expect(scaleTitle("CGI", "CGI")).toBe("");
  });
});
