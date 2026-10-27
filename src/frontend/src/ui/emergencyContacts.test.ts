import { describe, expect, it } from "vitest";
import { formatContactPhone, normalizeContactPhone } from "./contactPhone";

describe("emergency contact phones", () => {
  it.each([
    ["7", "+7"], ["8", "8"], ["9", "+79"], ["", ""],
    ["+7 (999) 123-45-67", "+79991234567"],
    ["8 (999) 123-45-67", "89991234567"], ["9991234567", "+79991234567"],
  ])("normalizes %s", (input, expected) => expect(normalizeContactPhone(input)).toBe(expected));

  it("formats both supported prefixes without changing the saved number", () => {
    expect(formatContactPhone("+79991234567")).toBe("+7 (999) 123-45-67");
    expect(formatContactPhone("89991234567")).toBe("8 (999) 123-45-67");
  });
});
