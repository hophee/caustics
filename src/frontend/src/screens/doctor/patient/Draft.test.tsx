import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { api, ApiError } from "../../../api/client";
import { Draft } from "./Draft";

vi.mock("./therapy/PatientFrame", () => ({
  PatientFrame: ({children}: {children: ReactNode}) => children,
  CardTabs: () => null,
}));
afterEach(() => vi.restoreAllMocks());
let sequence = 0;

function setup(withChanges: boolean) {
  const patientId = `confirmation-${++sequence}`;
  sessionStorage.setItem(`kompas.draft.${patientId}`, JSON.stringify({stopped: [], items: withChanges ? [{
    key: "new:1", labelId: "label-test", title: "Тестовый препарат", context: "", origin: {kind: "search"},
    regimen: {start: "2026-10-26", duration: "28", intakes: [{at: "08:00", dose: "20 мг"}]},
  }] : []}));
  const warning = {id: "contra-1", code: "excipient", kind: "patient_condition", severity: "contraindicated",
    targets: [{key: "new:1", box: "item"}], involves_new_item: withChanges,
    message: "Непереносимость: лактоза в составе препарата", sources: []};
  const report = {warnings: [warning], boxes: [], knowledge_version: "test"};
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.endsWith("/therapy/current")) return {items: [], base_token: "base-test"};
    if (path.endsWith("/prescriptions")) return {items: [], total: 0};
    if (path.startsWith("/drugs/")) return new Promise(() => {});
    if (path.endsWith("/profile")) return {suicide_risk: "low"};
    return {full_name: "Тестовый пациент", main_icd: "F41.1"};
  });
  const confirmations = vi.fn().mockRejectedValue(new ApiError(422, "invalid_input", "Назначение нельзя подтвердить", [
    {code: "justification_required", pointer: "contra-1", message: "Нужно обоснование (не короче 10 символов): непереносимость лактозы"},
  ]));
  vi.spyOn(api, "post").mockImplementation(async (path, body) => {
    if (path.endsWith("/basket/check")) return report;
    return confirmations(body);
  });
  render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
    <MemoryRouter initialEntries={[`/d/patients/${patientId}/draft`]}><Routes>
      <Route path="/d/patients/:patientId/draft" element={<Draft />} />
      <Route path="/d/patients/:patientId/therapy" element={<div>Сохранённое назначение</div>} />
    </Routes></MemoryRouter>
  </QueryClientProvider>);
  return confirmations;
}

describe("prescription confirmation feedback", () => {
  it("explains an unchanged prescription and shows warnings without sending an empty confirmation", async () => {
    const confirmations = setup(false);
    fireEvent.click(await screen.findByRole("button", {name: "Подтвердить назначение"}));
    const dialog = await screen.findByRole("dialog", {name: "В назначении нет изменений"});
    await within(dialog).findByText("Непереносимость: лактоза в составе препарата");
    expect(confirmations).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", {name: "Вернуться к аптечке"}));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("opens the justification dialog on server refusal and resubmits the explanation without bypassing validation", async () => {
    const confirmations = setup(true);
    fireEvent.click(await screen.findByRole("button", {name: "Подтвердить назначение"}));
    const dialog = await screen.findByRole("dialog", {name: "Назначение требует обоснования"});
    expect(confirmations).toHaveBeenCalledTimes(1);
    expect(within(dialog).getByRole("button", {name: "Подтвердить с обоснованием"})).toBeDisabled();
    fireEvent.change(within(dialog).getByLabelText("Обоснование назначения при противопоказании"), {target: {value: "Коротко"}});
    fireEvent.click(within(dialog).getByRole("button", {name: "Подтвердить с обоснованием"}));
    await waitFor(() => expect(confirmations).toHaveBeenCalledTimes(2));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    expect(within(dialog).getByText(/не короче 10 символов/)).toBeInTheDocument();
    confirmations.mockResolvedValueOnce({prescription_id: "confirmed-test"});
    fireEvent.change(within(dialog).getByLabelText("Обоснование назначения при противопоказании"), {target: {value: "Тестовое обоснование для проверки интерфейса"}});
    fireEvent.click(within(dialog).getByRole("button", {name: "Подтвердить с обоснованием"}));
    await screen.findByText("Сохранённое назначение");
    expect(confirmations.mock.calls[2]?.[0].justifications).toEqual({"contra-1": "Тестовое обоснование для проверки интерфейса"});
  });
});
