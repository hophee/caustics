import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { api } from "../../../api/client";
import { Safety } from "./Safety";

afterEach(() => vi.restoreAllMocks());
let sequence = 0;
function setup() {
  let profile = {birth_date: "1992-03-01", sex: "male", timezone: "Europe/Moscow",
    pregnant: "unknown", lactating: "unknown", substance_use: "unknown", suicide_risk: "unknown",
    diagnoses: [], allergies: null, past_intolerance: null, past_inefficacy: null,
    intolerant_excipients: null, weight_kg: null, current_medications_reviewed_at: null as string | null};
  let therapy: unknown[] = [];
  const get = vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path.endsWith("/cases/active")) throw {status: 404};
    if (path.endsWith("/profile")) return profile;
    if (path.endsWith("/therapy/current")) return {items: therapy, base_token: "current"};
    if (path === "/drugs/vocabulary") return {substances: ["сертралин"], trade_names: [], excipients: []};
    if (path === "/medications/search") return {items: [{code: "zoloft", name: "Золофт · таблетки → сертралин", label_id: "zoloft", substance: "сертралин"}]};
    if (path === "/knowledge/diagnoses") return [{code: "G40", name: "Эпилепсия"}];
    return {main_icd: null};
  });
  const put = vi.spyOn(api, "put").mockImplementation(async (path, body) => {
    if (path.endsWith("/therapy/current")) {
      therapy = (body as {items: unknown[]}).items;
      profile = {...profile, current_medications_reviewed_at: "2026-10-26T12:00:00Z"};
      return {items: therapy, base_token: "saved"};
    }
    profile = {...profile, ...(body as typeof profile)};
    return profile;
  });
  const post = vi.spyOn(api, "post");
  const id = `new-safety-${++sequence}`;
  render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
    <MemoryRouter initialEntries={[`/d/patients/${id}/safety`]}><Routes>
      <Route path="/d/patients/:patientId/safety" element={<Safety />} />
    </Routes></MemoryRouter>
  </QueryClientProvider>);
  return {get, put, post};
}

it("saves a selected ICD code and canonical MNN in a new patient card without opening a case", async () => {
  const {get, put, post} = setup();
  fireEvent.change(await screen.findByRole("combobox", {name: "Поиск по диагнозу или коду МКБ"}), {target: {value: "эпил"}});
  fireEvent.click(await screen.findByRole("button", {name: /G40.*Эпилепсия/}));
  expect(get).toHaveBeenCalledWith("/knowledge/diagnoses", {q: "эпил", scope: "contra"});
  fireEvent.change(screen.getByRole("combobox", {name: "Добавить текущий препарат: МНН или торговое название"}), {target: {value: "Золофт"}});
  fireEvent.click(await screen.findByRole("button", {name: "Золофт · таблетки → сертралин"}));
  expect(screen.getByText("сертралин", {selector: "b"})).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "Сохранить профиль"}));
  await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
  expect(put.mock.calls[0]?.[1]).toMatchObject({items: [{label_id: "zoloft", substance: "сертралин"}]});
  expect(put.mock.calls[1]?.[1]).toMatchObject({diagnoses: ["G40"]});
  expect(post).not.toHaveBeenCalled();
});

it("preserves unsaved profile edits when the medication list is saved separately", async () => {
  const {put} = setup();
  fireEvent.change(await screen.findByLabelText("Вес, кг"), {target: {value: "72"}});
  fireEvent.click(screen.getByRole("button", {name: "Сохранить текущую терапию"}));
  await screen.findByText("Текущая терапия сохранена по МНН. Проверки обновлены.");
  expect(screen.getByLabelText("Вес, кг")).toHaveValue("72");
  fireEvent.click(screen.getByRole("button", {name: "Сохранить профиль"}));
  await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
  expect(put.mock.calls[1]?.[1]).toMatchObject({weight_kg: 72});
});
