import { fireEvent, render, screen } from "@testing-library/react";
import { useState } from "react";
import { describe, expect, it, vi } from "vitest";
import { DateInput, DateTimeInput, TimeInput } from "./DateInput";

function Field({ kind, initial = "", min }: { kind: typeof DateInput; initial?: string; min?: string }) {
  const [value, setValue] = useState(initial);
  const Input = kind;
  return (
    <>
      <Input aria-label="field" value={value} min={min} onChange={setValue} />
      <output>{value}</output>
      <button type="button" onClick={() => setValue("")}>
        reset
      </button>
    </>
  );
}

const field = () => screen.getByLabelText<HTMLInputElement>("field");
const type = (text: string) => fireEvent.change(field(), { target: { value: text } });

describe("DateInput", () => {
  it("shows дд.мм.гггг and gives ISO", () => {
    render(<Field kind={DateInput} initial="1992-03-01" />);
    expect(field().value).toBe("01.03.1992");
    expect(field().type).toBe("text");
    type("25101990");
    expect(field().value).toBe("25.10.1990");
    expect(screen.getByRole("status")).toHaveTextContent("1990-10-25");
  });

  it("gives empty while incomplete or impossible, and keeps the typed text", () => {
    render(<Field kind={DateInput} initial="1992-03-01" />);
    type("2510");
    expect(field().value).toBe("25.10");
    expect(screen.getByRole("status")).toHaveTextContent("");
    type("31.02.2020");
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("follows a reset from outside", () => {
    render(<Field kind={DateInput} initial="1992-03-01" />);
    fireEvent.click(screen.getByText("reset"));
    expect(field().value).toBe("");
  });

  it("treats a date before min as not given", () => {
    render(<Field kind={DateInput} initial="2026-10-25" min="2026-10-01" />);
    type("30.09.2026");
    expect(field()).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("status")).toHaveTextContent("");
  });
});

describe("TimeInput and DateTimeInput", () => {
  it("use 24 hours", () => {
    render(<Field kind={TimeInput} initial="08:30" />);
    expect(field().value).toBe("08:30");
    type("2215");
    expect(screen.getByRole("status")).toHaveTextContent("22:15");
    type("25:00");
    expect(field()).toHaveAttribute("aria-invalid", "true");
  });

  it("joins date and time", () => {
    render(<Field kind={DateTimeInput} initial="2026-10-25T18:05" />);
    expect(field().value).toBe("25.10.2026 18:05");
    type("010120261930");
    expect(screen.getByRole("status")).toHaveTextContent("2026-01-01T19:30");
  });

  it("does not report a value it already has", () => {
    const onChange = vi.fn();
    render(<TimeInput aria-label="field" value="" onChange={onChange} />);
    type("1");
    expect(onChange).not.toHaveBeenCalled();
  });
});
