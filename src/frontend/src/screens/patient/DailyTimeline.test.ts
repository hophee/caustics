import { describe, expect, it } from "vitest";
import type { DiaryEntry } from "../../api/types";
import { binaryCell, moodCell } from "./DailyTimeline";

describe("daily timeline", () => {
  it("distinguishes a negative answer from no answer", () => {
    expect(binaryCell(true, "Да", "Нет")).toEqual({ tone: "yes", label: "Да" });
    expect(binaryCell(false, "Да", "Нет")).toEqual({ tone: "no", label: "Нет" });
    expect(binaryCell(undefined, "Да", "Нет")).toEqual({ tone: "missing", label: "Нет ответа" });
  });
  it("uses the three mood colors only for the new diary scale", () => {
    const entry = { schema_version: 2, mood: 0 } as DiaryEntry;
    expect(moodCell(entry).tone).toBe("depressed");
    expect(moodCell({ ...entry, mood: 1 }).tone).toBe("normal");
    expect(moodCell({ ...entry, mood: 2 }).tone).toBe("elevated");
    expect(moodCell({ ...entry, schema_version: 1, mood: 1 }).tone).toBe("missing");
    expect(moodCell({ ...entry, mood: null }).tone).toBe("missing");
  });
});
