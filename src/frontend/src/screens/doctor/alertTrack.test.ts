import { describe, expect, it } from "vitest";
import type { Alert } from "../../api/types";
import { alertSourceLabel, lastInteraction, since, trackLine, trackSteps } from "./alertTrack";

const now = new Date(2026, 9, 25, 9, 40);

function alert(over: Partial<Alert> & Record<string, unknown>): Alert {
  return {
    alert_id: "a",
    patient_id: "p",
    level: "urgent",
    status: "created",
    source: "scale:PHQ-9",
    source_id: "r",
    reason: "",
    details: {},
    undelivered: false,
    created_at: new Date(2026, 9, 24, 15, 47).toISOString(),
    accepted_at: null,
    handled_at: null,
    handled_note: null,
    ...over,
  } as Alert;
}

describe("trackSteps", () => {
  it("puts the time only on the last interaction and marks an accepted, unhandled alert overdue", () => {
    const steps = trackSteps(alert({ status: "accepted", accepted_at: new Date(2026, 9, 24, 15, 48).toISOString() }), now);
    expect(steps.map((s) => s.label)).toEqual(["создан", "отправлен", "доставлен", "принят вчера, 15:48", "не обработан, 17 ч"]);
    expect(steps.map((s) => s.done)).toEqual([true, true, true, true, false]);
    expect(steps[4]?.miss).toBe(true);
  });

  it("falls back to the newest known time when sent/delivered have no timestamp", () => {
    const steps = trackSteps(alert({ status: "delivered" }), now);
    expect(steps.map((s) => s.label)).toEqual(["создан вчера, 15:47", "отправлен", "доставлен", "не принят", "не обработан"]);
    expect(steps.some((s) => s.miss)).toBe(false);
  });

  it("uses delivered_at when the payload has it", () => {
    const steps = trackSteps(alert({ status: "delivered", delivered_at: new Date(2026, 9, 25, 9, 12).toISOString() }), now);
    expect(steps[0]?.label).toBe("создан");
    expect(steps[2]?.label).toBe("доставлен сегодня, 09:12");
  });

  it("shows exhausted delivery retries as overdue", () => {
    const steps = trackSteps(alert({ status: "sent", undelivered: true }), now);
    expect(steps[2]).toMatchObject({ label: "не доставлен", miss: true, done: false });
  });

  it("builds the phone line", () => {
    const line = trackLine(alert({ status: "accepted", accepted_at: new Date(2026, 9, 24, 15, 48).toISOString() }), now);
    expect(line).toEqual({ last: "принят вчера, 15:48", next: "не обработан 17 ч", miss: true });
  });
});

describe("helpers", () => {
  it("formats elapsed time", () => {
    expect(since(new Date(2026, 9, 25, 9, 15), now)).toBe("25 мин");
    expect(since(new Date(2026, 9, 22, 9, 40), now)).toBe("3 дн.");
  });

  it("finds the last interaction", () => {
    const handled = alert({ status: "handled", accepted_at: "2026-10-24T13:00:00Z", handled_at: "2026-10-24T14:00:00Z" });
    expect(lastInteraction(handled)).toEqual({ status: "handled", at: "2026-10-24T14:00:00Z" });
  });

  it("names the source", () => {
    expect(alertSourceLabel("scale:HDRS-17")).toBe("HDRS-17");
    expect(alertSourceLabel("diary_model")).toBe("Дневник");
  });
});
