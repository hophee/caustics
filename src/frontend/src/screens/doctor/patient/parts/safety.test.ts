import { describe, expect, it } from "vitest";
import type { Profile } from "../../../../api/types";
import { addItem, formOf, inVocabulary, listField, listValue, normalizeIcd, parseWeight, profileBody, suggest, validate, type Vocabulary } from "./safety";

const profile: Profile = {
  birth_date: "1992-03-01",
  sex: "female",
  timezone: "Europe/Moscow",
  pregnant: "no",
  lactating: "yes",
  substance_use: "no",
  suicide_risk: "low",
  diagnoses: ["F33.2", "I10"],
  allergies: [],
  intolerant_excipients: null,
  past_intolerance: ["пароксетин"],
  past_inefficacy: [],
  weight_kg: 58,
  current_medications_reviewed_at: null,
};

describe("safety form", () => {
  it("list facts keep null (unknown) apart from [] (none)", () => {
    expect(listField(null)).toEqual({ state: "unknown", items: [] });
    expect(listField([])).toEqual({ state: "none", items: [] });
    expect(listValue({ state: "unknown", items: ["x"] })).toBeNull();
    expect(listValue({ state: "none", items: ["x"] })).toEqual([]);
    expect(listValue({ state: "some", items: ["x"] })).toEqual(["x"]);
  });

  it("round-trips the profile into a full PUT body", () => {
    const form = formOf(profile, ["I10"]);
    expect(profileBody(profile, form, ["F33.2"])).toEqual({
      birth_date: "1992-03-01",
      sex: "female",
      timezone: "Europe/Moscow",
      pregnant: "no",
      lactating: "yes",
      substance_use: "no",
      suicide_risk: "low",
      diagnoses: ["F33.2", "I10"],
      allergies: [],
      intolerant_excipients: null,
      past_intolerance: ["пароксетин"],
      past_inefficacy: [],
      weight_kg: 58,
    });
  });

  it("keeps pregnancy answers for men untouched", () => {
    const man = { ...profile, sex: "male" as const, pregnant: "no" as const, lactating: "no" as const };
    const form = { ...formOf(man, []), pregnant: "unknown" as const };
    expect(profileBody(man, form, []).pregnant).toBe("no");
  });

  it("«Есть» needs at least one item; weight must be a number", () => {
    const form = formOf(profile, []);
    expect(validate(form)).toBeNull();
    const bad = { ...form, weight: "abc", lists: { ...form.lists, allergies: { state: "some" as const, items: [] } } };
    expect(validate(bad)).toEqual({ lists: { allergies: "Добавьте хотя бы одно значение" }, weight: "Вес в килограммах" });
  });

  it("helpers", () => {
    expect(addItem(["Лактоза"], " лактоза ")).toEqual(["Лактоза"]);
    expect(addItem([], " глютен ")).toEqual(["глютен"]);
    expect(normalizeIcd(" f41,1 ")).toBe("F41.1");
    expect(normalizeIcd("депрессия")).toBeNull();
    expect(parseWeight("")).toBeNull();
    expect(parseWeight("58,5")).toBe(58.5);
    expect(parseWeight("-1")).toBe("invalid");
  });
});

const vocabulary: Vocabulary = {
  substances: ["маритупирдин", "миртазапин", "пароксетин", "сертралин"],
  trade_names: [
    { name: "Авиандр", substances: ["маритупирдин"] },
    { name: "Золофт", substances: ["сертралин"] },
    { name: "Сертралин", substances: ["сертралин"] },
  ],
  excipients: ["лактоза", "этанол"],
};

describe("profile lists from the ОХЛП base", () => {
  it("a trade name suggests and stores its МНН", () => {
    const [hit] = suggest(vocabulary, "drugs", "авиа");
    expect(hit).toMatchObject({ label: "Авиандр", mnn: "маритупирдин", values: ["маритупирдин"] });
  });

  it("prefix matches come first; a trade name equal to its МНН is not repeated", () => {
    expect(suggest(vocabulary, "drugs", "сер").map((s) => s.label)).toEqual(["сертралин"]);
    expect(suggest(vocabulary, "drugs", "ин").map((s) => s.label)).toEqual(["маритупирдин", "миртазапин", "пароксетин", "сертралин"]);
    expect(suggest(vocabulary, "drugs", "  ")).toEqual([]);
  });

  it("excipients list on focus and filter by text", () => {
    expect(suggest(vocabulary, "excipients", "").map((s) => s.label)).toEqual(["лактоза", "этанол"]);
    expect(suggest(vocabulary, "excipients", "ЭТА").map((s) => s.values)).toEqual([["этанол"]]);
  });

  it("old free-text items are told apart from base names", () => {
    expect(inVocabulary(vocabulary, "drugs", "Сертралин")).toBe(true);
    expect(inVocabulary(vocabulary, "drugs", "золофт 50")).toBe(false);
    expect(inVocabulary(vocabulary, "excipients", "лактоза")).toBe(true);
  });
});
