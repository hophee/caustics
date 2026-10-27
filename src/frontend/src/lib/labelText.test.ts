import { describe, expect, it } from "vitest";
import { clip, labelText } from "./labelText";

describe("labelText", () => {
  it("drops Symbol-font bullets, list marks and checkboxes", () => {
    expect(labelText("- \uF0B7 Почечная недостаточность (СКФ <60 мл/мин)")).toBe("Почечная недостаточность (СКФ <60 мл/мин)");
    expect(labelText("\uF0BE Порфирия.; \uF0BE Анемия.; [ ] \uF0BE Бронхиальная астма.")).toBe("Порфирия.\nАнемия.\nБронхиальная астма.");
  });

  it("splits joined items and drops the empty МКБ prefix", () => {
    expect(labelText("МКБ-10: нет данных; показан для лечения:; [ ] шизофрении . □; \u00AD мании")).toBe("показан для лечения:\nшизофрении.\nмании");
  });

  it("flattens headings, tables and code fences", () => {
    expect(labelText("## Способ применения\n\nВнутрь")).toBe("Способ применения\nВнутрь");
    expect(labelText("| Клиренс (мл/мин) | Доза (мг/сут)^* |\n|------|------|\n| ≥ 60 | 300 |")).toBe("Клиренс (мл/мин) · Доза (мг/сут)*\n≥ 60 · 300");
    expect(labelText("макрогол 400,\n```\n\nапельсиновый желтый")).toBe("макрогол 400,\nапельсиновый желтый");
    expect(labelText("стабилизировано приемом . |")).toBe("стабилизировано приемом.");
  });

  it("keeps footnote stars and fixes backslashes", () => {
    expect(labelText("10 мг/кг/сут*, QT и\\или")).toBe("10 мг/кг/сут*, QT и/или");
  });
});

describe("clip", () => {
  it("never cuts inside a word", () => {
    const text = "Гиперчувствительность к действующему веществу";
    expect(clip(text, 30)).toBe("Гиперчувствительность к…");
    expect(clip(text, 100)).toBe(text);
  });

  it("prefers the end of a sentence", () => {
    expect(clip("Первое предложение тут. Второе предложение длинное", 34)).toBe("Первое предложение тут…");
  });
});
