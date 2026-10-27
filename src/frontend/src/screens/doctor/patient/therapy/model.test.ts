import { describe, expect, it } from "vitest";
import type { CheckReport, Warning } from "../../../../api/types";
import {
  buildRegimen,
  cardCheck,
  cleanQuote,
  confirmProblems,
  countWarnings,
  draftForCheck,
  draftForConfirm,
  justificationsPayload,
  newEntry,
  parseDose,
  parseDuration,
  parseTime,
  placeJustifications,
  probeWarnings,
  quoted,
  rebase,
  shortCause,
  unplacedWarnings,
  withProbes,
  type DraftEntry,
  type DraftState,
} from "./model";

const entry = (key: string, regimen: Partial<DraftEntry["regimen"]> = {}): DraftEntry => ({
  key,
  labelId: `L-${key}`,
  origin: { kind: "kr_suggestion", row_id: "kr-097" },
  title: "Амитриптилин (амитриптилин), таблетки",
  context: "КР 301_3",
  regimen: { intakes: [{ at: "21:00", dose: "25 мг" }], start: "2026-10-26", duration: "28", ...regimen },
});

function warning(id: string, severity: Warning["severity"], targets: Warning["targets"], extra: Partial<Warning> = {}): Warning {
  return {
    id,
    code: "c",
    kind: "patient_condition",
    severity,
    targets,
    message: "Грудное вскармливание: противопоказано (Амитриптилин)",
    sources: [{ document: "ОХЛП", ref: "L", section: "4.3", quote: "период грудного вскармливания" }],
    involves_new_item: true,
    ...extra,
  };
}

describe("dose, time and duration parsing", () => {
  it("reads mass doses with units, commas and bare numbers (мг)", () => {
    expect(parseDose("25 мг")).toEqual({ amount: "25", unit: "мг" });
    expect(parseDose("0,5 г")).toEqual({ amount: "0.5", unit: "г" });
    expect(parseDose("100мкг")).toEqual({ amount: "100", unit: "мкг" });
    expect(parseDose("25")).toEqual({ amount: "25", unit: "мг" });
    expect(parseDose("12.5 mg")).toEqual({ amount: "12.5", unit: "мг" });
  });

  it("rejects non-mass units, zero and garbage", () => {
    expect(parseDose("1 таблетка")).toBeNull();
    expect(parseDose("0 мг")).toBeNull();
    expect(parseDose("")).toBeNull();
    expect(parseDose("мг")).toBeNull();
  });

  it("normalises times and durations", () => {
    expect(parseTime("9:05")).toBe("09:05");
    expect(parseTime("21.00")).toBe("21:00");
    expect(parseTime("24:00")).toBeNull();
    expect(parseDuration("28 дней")).toBe(28);
    expect(parseDuration("0")).toBeNull();
    expect(parseDuration("2 недели")).toBeNull();
  });
});

describe("buildRegimen", () => {
  it("builds the backend shape from a complete form", () => {
    const { regimen } = buildRegimen({
      intakes: [
        { at: "08:00", dose: "25 мг" },
        { at: "21:00", dose: "50" },
      ],
      start: "2026-10-26",
      duration: "28",
    });
    expect(regimen).toEqual({
      intakes: [
        { at: "08:00", amount: "25", unit: "мг" },
        { at: "21:00", amount: "50", unit: "мг" },
      ],
      start: "2026-10-26",
      duration_days: 28,
    });
  });

  it("never fills a missing dose: an empty dose is an error, not a default", () => {
    const { regimen, errors } = buildRegimen({ intakes: [{ at: "21:00", dose: "" }], start: "2026-10-26", duration: "28" });
    expect(regimen).toBeNull();
    expect(errors.intakes[0]?.dose).toBe("Укажите дозу");
  });

  it("flags duplicate times, missing start and duration", () => {
    const { regimen, errors } = buildRegimen({
      intakes: [
        { at: "08:00", dose: "25" },
        { at: "8:00", dose: "25" },
      ],
      start: "",
      duration: "",
    });
    expect(regimen).toBeNull();
    expect(errors.intakes[1]?.at).toBe("Время повторяется");
    expect(errors.start).toBeTruthy();
    expect(errors.duration).toBe("Укажите длительность");
  });

  it("needs at least one intake", () => {
    expect(buildRegimen({ intakes: [], start: "2026-10-26", duration: "28" }).regimen).toBeNull();
  });
});

describe("draft payloads", () => {
  const state: DraftState = { items: [entry("n1"), entry("n2", { intakes: [{ at: "", dose: "" }], duration: "" })], stopped: ["ref-1"] };

  it("checks incomplete items with the probe intake and marks their dose unchecked", () => {
    const { draft, unchecked } = draftForCheck(state, "2026-10-25");
    expect(unchecked).toEqual(new Set(["n2"]));
    expect(draft.stopped).toEqual(["ref-1"]);
    expect(draft.new_items[0]?.regimen.intakes).toEqual([{ at: "21:00", amount: "25", unit: "мг" }]);
    expect(draft.new_items[1]?.regimen.intakes[0]?.at).toBe("00:00");
    expect(draft.new_items[1]?.regimen.duration_days).toBe(1);
    expect(draft.new_items[0]?.origin).toEqual({ kind: "kr_suggestion", row_id: "kr-097" });
  });

  it("refuses to build a confirm payload while any regimen is incomplete (the probe never reaches confirm)", () => {
    const { draft, errors } = draftForConfirm(state);
    expect(draft).toBeNull();
    expect(Object.keys(errors)).toEqual(["n2"]);
    const ok = draftForConfirm({ items: [entry("n1")], stopped: [] });
    expect(ok.draft?.new_items[0]?.regimen.duration_days).toBe(28);
  });

  it("new entries start without dose, time or duration", () => {
    const e = newEntry(state, { labelId: "X", title: "X", origin: { kind: "search" }, context: "" }, "2026-10-25");
    expect(e.key).toBe("n3");
    expect(e.regimen).toEqual({ intakes: [{ at: "", dose: "" }], start: "2026-10-25", duration: "" });
  });

  it("drops stops of drugs no longer in the current therapy", () => {
    expect(rebase(state, ["ref-2"]).stopped).toEqual([]);
    expect(rebase(state, ["ref-1"])).toBe(state);
  });
});

describe("mapping the check report to cards", () => {
  const report: CheckReport = {
    knowledge_version: "k",
    warnings: [
      warning("w-red", "contraindicated", [{ item_key: "n1", part: "item" }]),
      warning("w-dose", "no_data", [{ item_key: "n1", part: "dose" }], { kind: "dose", message: "Максимальная доза не распознана" }),
      warning("w-pair", "caution", [
        { item_key: "current:a", part: "item" },
        { item_key: "n1", part: "item" },
      ], { kind: "interaction", message: "Амитриптилин + Миртазапин: с осторожностью" }),
      warning("w-orphan", "caution", [{ item_key: "current:gone", part: "item" }]),
    ],
    boxes: [
      { key: "n1", part: "item", severity: "contraindicated" },
      { key: "current:a", part: "item", severity: "caution" },
    ],
  };

  it("boxes each card and its dose field and lists every warning that targets it, worst first", () => {
    const card = cardCheck(report, "n1");
    expect(card.item).toBe("contraindicated");
    expect(card.dose).toBe("no_data");
    expect(card.warnings.map((w) => w.id)).toEqual(["w-red", "w-pair", "w-dose"]);
    expect(card.doseWarnings.map((w) => w.id)).toEqual(["w-dose"]);
    expect(cardCheck(report, "current:a").warnings.map((w) => w.id)).toEqual(["w-pair"]);
  });

  it("reports the dose of a probed card as unchecked, never as clean", () => {
    expect(cardCheck(report, "n1", new Set(["n1"])).dose).toBe("unchecked");
    expect(cardCheck(undefined, "n9", new Set(["n9"])).dose).toBe("unchecked");
  });

  it("keeps warnings for cards not on the page visible", () => {
    expect(unplacedWarnings(report, ["n1", "current:a"]).map((w) => w.id)).toEqual(["w-orphan"]);
  });

  it("counts by severity and interactions", () => {
    expect(countWarnings(report)).toEqual({ contraindicated: 1, caution: 2, no_data: 1, interactions: 1 });
  });

  it("short causes for pills", () => {
    expect(shortCause(report.warnings[0] as Warning)).toBe("Грудное вскармливание");
    expect(shortCause(report.warnings[2] as Warning)).toBe("Взаимодействие");
  });
});

describe("picker probes", () => {
  it("adds one probe per label after the draft items", () => {
    const base = draftForCheck({ items: [entry("n1")], stopped: [] }, "2026-10-25").draft;
    const { draft, keyOf } = withProbes(base, ["A", "B", "A"], "2026-10-25");
    expect(draft.new_items.map((i) => i.key)).toEqual(["n1", "probe:0", "probe:1"]);
    expect(keyOf.get("B")).toBe("probe:1");
  });

  it("assigns warnings to a probe only when no other probe is involved", () => {
    const report: CheckReport = {
      knowledge_version: "k",
      boxes: [],
      warnings: [
        warning("own", "contraindicated", [{ item_key: "probe:0", part: "item" }]),
        warning("with-current", "caution", [
          { item_key: "current:a", part: "item" },
          { item_key: "probe:1", part: "item" },
        ]),
        warning("between-probes", "contraindicated", [
          { item_key: "probe:0", part: "item" },
          { item_key: "probe:1", part: "item" },
        ]),
      ],
    };
    const result = probeWarnings(report, ["probe:0", "probe:1", "probe:2"]);
    expect(result.get("probe:0")?.map((w) => w.id)).toEqual(["own"]);
    expect(result.get("probe:1")?.map((w) => w.id)).toEqual(["with-current"]);
    expect(result.get("probe:2")).toEqual([]);
  });
});

describe("justifications", () => {
  const details = [
    { code: "justification_required", message: "Нужно обоснование (не короче 10 символов): …", pointer: "w-red" },
    { code: "regimen_invalid", message: "длительность курса должна быть больше нуля", pointer: "n1.regimen" },
  ];

  it("splits the 422 details into requested justifications and other problems", () => {
    const p = confirmProblems(details);
    expect([...p.justifications.keys()]).toEqual(["w-red"]);
    expect(p.other).toEqual([{ code: "regimen_invalid", message: "длительность курса должна быть больше нуля", pointer: "n1.regimen" }]);
  });

  it("places each requested justification on the first new card its warning targets", () => {
    const report: CheckReport = {
      knowledge_version: "k",
      boxes: [],
      warnings: [
        warning("pair", "contraindicated", [
          { item_key: "current:a", part: "item" },
          { item_key: "n2", part: "item" },
        ]),
        warning("cur", "contraindicated", [{ item_key: "current:a", part: "item" }]),
      ],
    };
    const placed = placeJustifications(["pair", "cur", "gone"], report, ["n1", "n2"], ["current:a"]);
    expect(placed.byKey.get("n2")).toEqual(["pair"]);
    expect(placed.byKey.get("current:a")).toEqual(["cur"]);
    expect(placed.unplaced).toEqual(["gone"]);
  });

  it("sends only the justifications the server asked for, trimmed and non-empty", () => {
    expect(justificationsPayload(["a", "b"], { a: "  решение обсуждено  ", b: "  ", c: "лишнее" })).toEqual({ a: "решение обсуждено" });
  });
});

describe("quotes", () => {
  it("drops list bullets and private-use glyphs, wraps once", () => {
    expect(cleanQuote("-  период грудного вскармливания.")).toBe("период грудного вскармливания.");
    expect(quoted("«…рекомендуется…»")).toBe("«…рекомендуется…»");
    expect(quoted("ИМАО")).toBe("«ИМАО»");
  });
});
