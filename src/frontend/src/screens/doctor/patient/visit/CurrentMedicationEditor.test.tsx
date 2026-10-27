import { afterEach, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { api } from "../../../../api/client";
import { CurrentMedicationEditor } from "./CurrentMedicationEditor";

afterEach(() => vi.restoreAllMocks());
let sequence = 0;
function setup() {
  let items: {ref: string; label_id: string | null; substance: string}[] = [];
  vi.spyOn(api, "get").mockImplementation(async (path) => {
    if (path === "/drugs/vocabulary") return {substances: ["сертралин", "вещество а", "вещество б"], aliases: {}};
    if (path === "/medications/search") return {items: [
      {code: "brand", name: "Тестовый бренд · таблетки → сертралин", label_id: "brand", substance: "сертралин"},
      {code: "combo", name: "Тестовая комбинация · таблетки → вещество а + вещество б", label_id: null, substance: "вещество а + вещество б"},
    ]};
    return {items, base_token: "base"};
  });
  const put = vi.spyOn(api, "put").mockImplementation(async (_path, raw) => {
    const body = raw as {items: typeof items};
    items = body.items.map((i, index) => ({...i, ref: `saved-${index}`}));
    return {items, base_token: "next"};
  });
  render(<QueryClientProvider client={new QueryClient({defaultOptions: {queries: {retry: false}}})}>
    <CurrentMedicationEditor patientId={`medication-test-${++sequence}`} />
  </QueryClientProvider>);
  return put;
}

it("shows and saves MNN after choosing a trade name, then removes the entry", async () => {
  const put = setup();
  fireEvent.change(await screen.findByRole("combobox"), {target: {value: "Тестовый"}});
  fireEvent.click(await screen.findByRole("button", {name: "Тестовый бренд · таблетки → сертралин"}));
  expect(screen.getByText("сертралин", {selector: "b"})).toBeInTheDocument();
  expect(screen.queryByText("Тестовый бренд")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "Сохранить текущую терапию"}));
  await screen.findByText("Текущая терапия сохранена по МНН. Проверки обновлены.");
  expect(put.mock.calls[0]?.[1]).toMatchObject({items: [{label_id: "brand", substance: "сертралин"}]});
  fireEvent.click(screen.getByRole("button", {name: "Убрать из текущей терапии"}));
  fireEvent.click(screen.getByRole("button", {name: "Сохранить текущую терапию"}));
  await waitFor(() => expect(put).toHaveBeenCalledTimes(2));
  expect(put.mock.calls[1]?.[1]).toMatchObject({items: []});
});

it("retains every active substance when selecting a combination", async () => {
  const put = setup();
  fireEvent.change(await screen.findByRole("combobox"), {target: {value: "комбинация"}});
  fireEvent.click(await screen.findByRole("button", {name: "Тестовая комбинация · таблетки → вещество а + вещество б"}));
  expect(screen.getByText("вещество а + вещество б", {selector: "b"})).toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", {name: "Сохранить текущую терапию"}));
  await waitFor(() => expect(put).toHaveBeenCalledTimes(1));
  expect(put.mock.calls[0]?.[1]).toMatchObject({items: [{label_id: null, substance: "вещество а + вещество б"}]});
});
