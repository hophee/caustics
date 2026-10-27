import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { AlertLevelPill } from "./levels";

describe("alert level pill", () => {
  it("names the urgent scenario (D-132)", () => {
    render(<>
      <AlertLevelPill level="urgent" scenario="danger_now" />
      <AlertLevelPill level="urgent" scenario="check_soon" />
      <AlertLevelPill level="urgent" />
      <AlertLevelPill level="high" scenario={null} />
    </>);
    expect(screen.getByText("Срочно — опасность сейчас")).toBeTruthy();
    expect(screen.getByText("Срочно — уточнить состояние")).toBeTruthy();
    expect(screen.getByText("Срочно")).toBeTruthy();
    expect(screen.getByText("Высокий")).toBeTruthy();
  });
});
