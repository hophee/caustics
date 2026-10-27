import { afterEach, describe, expect, it, vi } from "vitest";
import { createElement, type ReactNode } from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { api } from "../../api/client";
import { keys } from "../../api/queries";
import { VisitWorkspace } from "./patient/VisitWorkspace";
import { composeNote } from "./alertDecision";
import { newPatientBody, validateNewPatient } from "./newPatient";

vi.mock("./patient/therapy/PatientFrame", () => ({
  PatientFrame: ({ children }: { children: ReactNode }) => children,
}));

afterEach(() => vi.restoreAllMocks());

describe("starting a visit", () => {
  const active = { case_id: "case-1", patient_id: "patient-1", status: "active" as const, opened_at: "2026-10-26T09:00:00Z", closed_at: null };

  function setup(hasCase = false) {
    sessionStorage.clear();
    vi.spyOn(api, "get").mockImplementation(async (path) => {
      if (path.endsWith("/cases/active")) {
        if (hasCase) return active;
        throw { status: 404 };
      }
      if (path.endsWith("/profile")) return {
        birth_date: "1992-03-01", sex: "female", timezone: "Europe/Moscow",
        pregnant: "unknown", lactating: "unknown", substance_use: "unknown", suicide_risk: "unknown",
        diagnoses: [], allergies: null, past_intolerance: null, past_inefficacy: null,
        intolerant_excipients: null, weight_kg: null, current_medications_reviewed_at: null,
      };
      if (path === "/drugs/vocabulary") return {substances: [], trade_names: [], excipients: [], aliases: {}};
      if (path.endsWith("/therapy/current")) return {items: [], base_token: "test"};
      if (path.endsWith("/prescriptions") || path.endsWith("/results")) return {items: [], total: 0};
      return [];
    });
    const post = vi.spyOn(api, "post").mockImplementation(async () => {
      hasCase = true;
      return active;
    });
    const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(createElement(QueryClientProvider, { client },
      createElement(MemoryRouter, { initialEntries: ["/d/patients/patient-1/visit"] },
        createElement(Routes, null,
          createElement(Route, { path: "/d/patients/:patientId/visit", element: createElement(VisitWorkspace) }),
          createElement(Route, { path: "/d/patients/:patientId", element: createElement("div", null, "Карточка пациента") }),
        ),
      ),
    ));
    return { post, client };
  }

  it("asks before creating a case for a patient without an active case, then opens the visit", async () => {
    const { post, client } = setup();
    await screen.findByRole("dialog", { name: "Открыть новый медицинский случай?" });
    expect(post).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: "Да" }));
    await screen.findByRole("heading", { name: "Приём" });
    expect(post).toHaveBeenCalledExactlyOnceWith("/patients/patient-1/cases");
    expect(client.getQueryData(keys.activeCase("patient-1"))).toEqual(active);
  });

  it("returns to the patient without creating a case on cancel", async () => {
    const { post } = setup();
    fireEvent.click(await screen.findByRole("button", { name: "Отмена" }));
    await screen.findByText("Карточка пациента");
    expect(post).not.toHaveBeenCalled();
  });

  it("opens an existing case without confirmation or creation", async () => {
    const { post } = setup(true);
    await screen.findByRole("heading", { name: "Приём" });
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
    expect(post).not.toHaveBeenCalled();
  });

  it("keeps the confirmation open after a failed creation and allows retry", async () => {
    const { post } = setup();
    post.mockRejectedValueOnce(new Error("Ошибка создания"));
    fireEvent.click(await screen.findByRole("button", { name: "Да" }));
    await waitFor(() => expect(screen.getByRole("button", { name: "Да" })).toBeEnabled());
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "Да" }));
    await screen.findByRole("heading", { name: "Приём" });
    expect(post).toHaveBeenCalledTimes(2);
  });
});

describe("alert decision note", () => {
  it("lists the chosen actions in display order, then risk and the note", () => {
    const note = composeNote({ actions: ["visit", "contacted"], risk: "low", note: "  перенесли приём  " }, "female");
    expect(note).toBe("Связь с пациенткой установлена\nНазначен внеочередной приём\nСуицидальный риск: низкий\nперенесли приём");
  });

  it("is empty when nothing was recorded", () => {
    expect(composeNote({ actions: [], risk: null, note: " " }, "male")).toBe("");
  });
});

describe("new patient form", () => {
  const today = new Date(2026, 9, 25);

  it("requires every field", () => {
    expect(validateNewPatient({ lastName: " ", firstName: " ", middleName: "", birthDate: "", sex: "", timezone: "" }, today)).toEqual({
      lastName: "Укажите фамилию", firstName: "Укажите имя",
      birthDate: "Укажите дату рождения",
      sex: "Укажите пол",
      timezone: "Укажите город",
    });
  });

  it("rejects a future birth date", () => {
    expect(validateNewPatient({ lastName: "А", firstName: "Б", middleName: "", birthDate: "2027-01-01", sex: "male", timezone: "Europe/Moscow" }, today).birthDate).toBeDefined();
  });

  it("builds the request body", () => {
    const form = { lastName: "  Смирнова ", firstName: " Анна ", middleName: "", birthDate: "1992-03-01", sex: "female" as const, timezone: "Asia/Omsk" };
    expect(validateNewPatient(form, today)).toEqual({});
    expect(newPatientBody(form)).toEqual({ last_name: "Смирнова", first_name: "Анна", middle_name: "", birth_date: "1992-03-01", sex: "female", timezone: "Asia/Omsk" });
  });
});

describe("patient name limits", () => {
  for (const field of ["lastName", "firstName", "middleName"] as const) {
    it("limits " + field + " to 60 characters", () => {
      const form = { lastName: "Иванов", firstName: "Иван", middleName: "", birthDate: "1990-01-01", sex: "male" as const, timezone: "Europe/Moscow" };
      expect(validateNewPatient({ ...form, [field]: "я".repeat(60) })[field]).toBeUndefined();
      expect(validateNewPatient({ ...form, [field]: "я".repeat(61) })[field]).toBeDefined();
    });
  }
});
