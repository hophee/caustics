import { describe, expect, it } from "vitest";
import { age, date, initials, longDay, plural, when } from "./format";

describe("format", () => {
  it("formats calendar dates without a time-zone shift", () => {
    expect(date("1992-03-01")).toBe("01.03.1992");
  });

  it("counts age up to the birthday", () => {
    expect(age("1992-03-01", new Date(2026, 1, 28))).toBe("33 года");
    expect(age("1992-03-01", new Date(2026, 2, 1))).toBe("34 года");
    expect(age("2005-01-01", new Date(2026, 5, 1))).toBe("21 год");
    expect(age("2014-01-01", new Date(2026, 5, 1))).toBe("12 лет");
  });

  it("uses Russian plural forms", () => {
    expect(plural(1, "день", "дня", "дней")).toBe("день");
    expect(plural(3, "день", "дня", "дней")).toBe("дня");
    expect(plural(11, "день", "дня", "дней")).toBe("дней");
    expect(plural(22, "день", "дня", "дней")).toBe("дня");
  });

  it("says today and yesterday", () => {
    const now = new Date(2026, 9, 25, 12, 0);
    expect(when(new Date(2026, 9, 25, 8, 14), now)).toBe("сегодня, 08:14");
    expect(when(new Date(2026, 9, 24, 22, 10), now)).toBe("вчера, 22:10");
    expect(when(new Date(2026, 9, 20, 9, 5), now)).toBe("20.10, 09:05");
  });

  it("writes the long day", () => {
    expect(longDay("2026-10-25")).toBe("Воскресенье, 25 октября");
  });

  it("derives initials", () => {
    expect(initials("Анна Смирнова")).toBe("АС");
  });
});
