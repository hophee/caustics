import { describe, expect, it } from "vitest";
import type { Candidate, KrRow, Selection } from "../../../../api/types";
import type { PrescriptionVersion } from "./api";
import { joinCurrent, justifiedIn } from "./current";
import { courseText, doseText, normalizeRegimen, periodText } from "./regimen";
import { groupCandidates, highlight, matches, shortSource } from "./selection";

describe("regimens from the API", () => {
  it("reads the prescription shape", () => {
    const r = normalizeRegimen({ unit: "мг", start: "2026-10-23", intakes: [{ at: "21:00", amount: "25" }], duration_days: 28 });
    expect(doseText(r)).toBe("25 мг в 21:00");
    expect(courseText(r)).toBe("23.10 — 19.11 · 28 дней");
    expect(periodText(r)).toBe("с 23 октября по 19 ноября");
  });

  it("reads the current-therapy (domain) shape", () => {
    const r = normalizeRegimen({
      schedule: { kind: "daily", intakes: [{ at: "08:00:00", amount_mg: 25.0 }, { at: "21:00:00", amount_mg: 50 }] },
      start: "2026-10-23",
      duration_days: 1,
    });
    expect(doseText(r)).toBe("25 мг в 08:00, 50 мг в 21:00");
    expect(courseText(r)).toBe("23.10 — 23.10 · 1 день");
  });

  it("reads PRN and says when the schedule is unknown", () => {
    expect(doseText(normalizeRegimen({ schedule: { kind: "prn", dose_mg: 0.5, max_doses_per_day: 2 }, start: "2026-10-23", duration_days: 7 }))).toBe(
      "0.5 мг при необходимости, до 2 раз в сутки",
    );
    expect(doseText(normalizeRegimen(null))).toBe("Схема неизвестна");
  });
});

const row = (id: string, mnn: string[]): KrRow => ({
  id,
  code: "F33.2",
  stage: "1 этап: купирующая",
  condition: "",
  scheme: "Монотерапия",
  window: "Антидепрессант",
  substances_text: mnn.join(", "),
  targets: mnn.map((m) => ({ kind: "substance", mnn: m, off_label: false, evidence: null })),
  evidence: "A / 1",
  source: "КР 301_3 (F32:F33 .pdf), п. 3.1.3",
  quote: "«…»",
});

const cand = (labelId: string, substance: string, r: KrRow): Candidate =>
  ({
    label: { id: labelId, trade_name: labelId, form: "таблетки", substances: [substance], atc: "N06" },
    row: r,
    off_label_in_kr: false,
    evidence: r.evidence,
  }) as unknown as Candidate;

describe("selection view", () => {
  it("groups candidates by row and МНН in the order the row names them, without duplicate labels", () => {
    const r = row("kr-097", ["венлафаксин", "амитриптилин"]);
    const selection = {
      rows: [r],
      candidates: [cand("A1", "амитриптилин", r), cand("V1", "венлафаксин", r), cand("V1", "венлафаксин", r), cand("V2", "венлафаксин", r)],
      advice: [],
      restrictions: [],
      restrictions_to_clarify: [],
    } as Selection;
    const groups = groupCandidates(selection);
    expect(groups[0]?.title).toBe("Антидепрессант, монотерапия");
    expect(groups[0]?.groups.map((g) => [g.mnn, g.candidates.length])).toEqual([
      ["венлафаксин", 2],
      ["амитриптилин", 1],
    ]);
  });

  it("keeps stages and their role windows separate, including maintenance and second choice", () => {
    const first = row("kr-1", ["сертралин"]);
    const prophylaxis = { ...row("kr-2", ["миртазапин"]), stage: "Профилактическая" };
    const resistant = { ...row("kr-3", ["венлафаксин"]), stage: "2 этап", condition: "Неэффективны 3–4 недели АД" };
    const selection = {
      rows: [first, resistant, prophylaxis],
      candidates: [cand("S1", "сертралин", first), cand("M1", "миртазапин", prophylaxis), cand("V1", "венлафаксин", resistant)],
      advice: [],
      restrictions: [],
      restrictions_to_clarify: [],
    } as Selection;
    const groups = groupCandidates(selection);
    expect(groups.map((g) => [g.row.id, g.groups.map((m) => m.mnn)])).toEqual([
      ["kr-1", ["сертралин"]],
      ["kr-3", ["венлафаксин"]],
      ["kr-2", ["миртазапин"]],
    ]);
  });

  it("matches by trade name, МНН or АТХ and highlights the hit", () => {
    expect(matches("n06a", { trade_name: "X", substances: [], atc: "N06AA09" })).toBe(true);
    expect(matches("амитр", { trade_name: "Саротен", substances: ["амитриптилин"], atc: null })).toBe(true);
    expect(highlight("Амитриптилин", "амитр")).toEqual(["", "Амитр", "иптилин"]);
    expect(shortSource("КР 301_3 (F32:F33 .pdf), п. 3.1.3")).toBe("КР 301_3, п. 3.1.3");
  });
});

const version = (id: string, n: number, courses: string[], justified: string[] = []): PrescriptionVersion => ({
  prescription_id: id,
  version: n,
  confirmed_at: `2026-10-2${n}T10:00:00Z`,
  knowledge_version: "k",
  items: courses.map((c) => ({
    course_id: c,
    label_id: `L-${c}`,
    substance: "амитриптилин",
    trade_name: `T-${c}-v${n}`,
    form: "таблетки",
    regimen: null,
    is_new: true,
    provenance: null,
    suggested_dose_text: null,
  })),
  stopped_course_ids: [],
  warnings: [],
  justifications: justified.map((w) => ({ warning_id: w, text: `обоснование v${n}` })),
});

describe("current therapy view", () => {
  it("takes names from the newest version that lists the course; reported drugs stay nameless", () => {
    const versions = [version("p2", 2, ["c1"], ["w1"]), version("p1", 1, ["c1", "c2"], ["w1", "w2"])];
    const views = joinCurrent(
      [
        { ref: "c1", label_id: "L-c1", substance: null, regimen: null, patient_reported: false },
        { ref: "r9", label_id: null, substance: "сертралин", regimen: null, patient_reported: true },
      ],
      versions,
    );
    expect(views[0]?.key).toBe("current:c1");
    expect(views[0]?.item?.trade_name).toBe("T-c1-v2");
    expect(views[1]?.item).toBeNull();
    const justified = justifiedIn(versions);
    expect(justified.get("w1")?.version.version).toBe(2);
    expect(justified.get("w2")?.version.version).toBe(1);
  });
});
