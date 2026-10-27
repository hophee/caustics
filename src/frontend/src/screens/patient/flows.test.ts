import { describe, expect, it } from "vitest";
import type { Assignment, Dose, PrescriptionLine, ScaleForm } from "../../api/types";
import {
  EMPTY_DRAFT,
  STEPS,
  buildDiaryPayload,
  firstStep,
  localDay,
  moodLabel,
  nextStep,
  parseMood,
  parseSleepDuration,
  sleepDurationLabel,
  prevStep,
  progress,
  stepNumber,
  toggle,
} from "./diaryFlow";
import { calendarDays, calendarRange, dayMark, doseStateLabel, lineSchedule, lineTitle, reminderText } from "./doses";
import { answeredCount, assignmentLine, buildResponsePayload, isComplete } from "./questionnaireFlow";

describe("diary flow", () => {
  it("walks seven diary steps", () => {
    expect(STEPS).toEqual(["mood", "anxiety", "irritability", "sleep", "unusual", "self_harm", "text"]);
    expect(nextStep("mood")).toBe("anxiety");
    expect(nextStep("sleep")).toBe("unusual");
    expect(nextStep("unusual")).toBe("self_harm");
    expect(nextStep("self_harm")).toBe("text");
    expect(nextStep("text")).toBeNull();
    expect(prevStep("mood")).toBeNull();
    expect(prevStep("anxiety")).toBe("mood");
    expect(stepNumber("anxiety")).toBe(2);
    expect(progress("anxiety")).toBe("28.6%");
    expect(progress("text")).toBe("100%");
  });

  it("skips the mood step when the mood came from «Сегодня»", () => {
    expect(firstStep(EMPTY_DRAFT)).toBe("mood");
    expect(firstStep({ ...EMPTY_DRAFT, mood: parseMood("1") })).toBe("anxiety");
    expect(parseMood("11")).toBeNull();
    expect(parseMood("-1")).toBeNull();
    expect(parseMood(null)).toBeNull();
  });

  it("builds the POST /me/diary body; skipped answers stay null, never «no»", () => {
    const body = buildDiaryPayload(EMPTY_DRAFT, "2026-10-25", "sid-1");
    expect(body).toEqual({
      submission_id: "sid-1",
      day: "2026-10-25",
      mood: null,
      anxiety: null,
      irritability: null,
      sleep_hours: null,
      unusual_experiences: null,
      self_harm_thoughts: null,
      schema_version: 2,
      text: null,
    });
    const filled = buildDiaryPayload(
      { mood: 0, anxiety: 3, irritability: 0, sleep_hours: 6.5, unusual_experiences: false, self_harm_thoughts: true, text: "  плохо спала " },
      "2026-10-25",
      "sid-2",
    );
    expect(filled.irritability).toBe(0);
    expect(filled.unusual_experiences).toBe(false);
    expect(filled.self_harm_thoughts).toBe(true);
    expect(filled.sleep_hours).toBe(6.5);
    expect(filled.text).toBe("плохо спала");
  });

  it("toggles complaints and labels stored moods", () => {
    expect(toggle(["a"], "b")).toEqual(["a", "b"]);
    expect(toggle(["a", "b"], "a")).toEqual(["b"]);
    expect(moodLabel(0)).toBe("Депрессивное");
    expect(moodLabel(1)).toBe("Нормальное");
    expect(moodLabel(2)).toBe("Повышенное");
    expect(moodLabel(null)).toBeNull();
    expect(localDay(new Date(2026, 9, 5, 23, 30))).toBe("2026-10-05");
  });
});

const PHQ: ScaleForm = {
  code: "PHQ-9",
  name: "PHQ-9",
  version: 1,
  filled_by: "patient",
  items: [1, 2, 3].map((id) => ({ id, text: `q${id}`, options: [0, 1, 2, 3].map((value) => ({ value, label: String(value) })) })),
};

describe("questionnaire flow", () => {
  it("builds the POST /me/responses body keyed by item id", () => {
    const answers = { 1: 0, 2: 3, 3: 1, 99: 2 };
    expect(isComplete(PHQ, answers)).toBe(true);
    expect(buildResponsePayload(PHQ, answers, "sid", "asg")).toEqual({
      submission_id: "sid",
      scale_code: "PHQ-9",
      answers: { "1": 0, "2": 3, "3": 1 },
      assignment_id: "asg",
    });
  });

  it("knows when answers are missing", () => {
    expect(answeredCount(PHQ, { 1: 0 })).toBe(1);
    expect(isComplete(PHQ, { 1: 0, 3: 0 })).toBe(false);
  });

  it("describes an assignment without any result", () => {
    const once = { assignment_id: "a", scale_code: "PHQ-9", mode: "once", every_days: null, starts_on: "2026-10-24", due_days: 3 } as Assignment;
    expect(assignmentLine(once, { items: 9 })).toBe("9 вопросов · около 3 минут · до 27 октября");
    expect(assignmentLine({ ...once, mode: "repeat", every_days: 14 }, { items: 7 })).toBe("7 вопросов · около 2 минут");
  });
});

describe("doses", () => {
  it("labels marked doses and leaves markable ones to the buttons", () => {
    expect(doseStateLabel("on_time")).toBe("Принял");
    expect(doseStateLabel("late")).toBe("Принял с опозданием");
    expect(doseStateLabel("skipped")).toBe("Пропустил");
    expect(doseStateLabel("unmarked")).toBe("Нет отметки");
    expect(doseStateLabel("due")).toBeNull();
    expect(doseStateLabel("upcoming")).toBeNull();
  });

  it("marks calendar days", () => {
    expect(dayMark([{ state: "on_time" }, { state: "late" }], false)).toBe("tk");
    expect(dayMark([{ state: "on_time" }, { state: "skipped" }], false)).toBe("sk");
    expect(dayMark([{ state: "unmarked" }], false)).toBe("nm");
    expect(dayMark([], false)).toBe("");
    expect(dayMark([{ state: "upcoming" }], true)).toBe("fu");
  });

  it("lays out three Monday-first weeks ending with today's week", () => {
    const today = new Date(2026, 9, 25); // Sunday
    expect(calendarRange(today)).toMatchObject({ since: "2026-10-05", until: "2026-10-25" });
    const doses = [
      { day: "2026-10-24", state: "skipped" },
      { day: "2026-10-25", state: "due" },
    ] as Dose[];
    const days = calendarDays(doses, today);
    expect(days).toHaveLength(21);
    expect(days[0]?.day).toBe("2026-10-05");
    expect(days.find((d) => d.day === "2026-10-24")?.mark).toBe("sk");
    expect(days.find((d) => d.today)).toMatchObject({ day: "2026-10-25", mark: "fu" });
  });

  it("describes prescription lines and the reminder rule", () => {
    const line = { trade_name: "Амитриптилин", form: "таблетки", intakes: [{ at: "21:00", amount_mg: 25 }], as_needed: null, start: "2026-10-23", end: "2026-11-19" } as PrescriptionLine;
    expect(lineTitle(line)).toBe("Амитриптилин, таблетки 25 мг");
    expect(lineSchedule(line)).toBe("25 мг в 21:00 · с 23 октября по 19 ноября");
    const rule = { enabled: true, first_offset_minutes: 0, repeat_every_minutes: 30, max_repeats: 2 };
    expect(reminderText(rule, ["21:00"])).toBe("в 21:00, повтор через 30 минут, не больше 2 раз");
    expect(reminderText({ ...rule, first_offset_minutes: -15, max_repeats: 0 }, ["21:00"])).toBe("за 15 минут до приёма (21:00), без повторов");
    expect(reminderText({ ...rule, enabled: false }, [])).toBe("выключено");
  });
});

describe("sleep duration", () => {
  it("defaults missing minutes to 00 and keeps minute precision", () => {
    expect(parseSleepDuration("7", "")).toBe(7);
    expect(sleepDurationLabel(parseSleepDuration("7", "")!)).toBe("07:00");
    expect(sleepDurationLabel(parseSleepDuration("7", "05")!)).toBe("07:05");
    expect(sleepDurationLabel(parseSleepDuration("0", "30")!)).toBe("00:30");
    expect(parseSleepDuration("", "")).toBeNull();
    expect(parseSleepDuration("24", "00")).toBe(24);
  });
  it("rejects invalid hours and minutes instead of saving a different duration", () => {
    expect(parseSleepDuration("24", "01")).toBeNaN();
    expect(parseSleepDuration("7", "60")).toBeNaN();
    expect(parseSleepDuration("-1", "00")).toBeNaN();
    expect(parseSleepDuration("", "30")).toBeNaN();
    expect(parseSleepDuration("ab", "00")).toBeNaN();
  });
});
