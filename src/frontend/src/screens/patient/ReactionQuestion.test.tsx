import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, expect, test, vi } from "vitest";
import { api } from "../../api/client";
import { ReactionQuestion } from "./ReactionQuestion";

vi.mock("./patientApi", () => ({ usePrescription: () => ({ data: [{ trade_name: "Трамал" }] }) }));
beforeEach(() => { vi.restoreAllMocks(); });
test("Yes notifies first; details preserve trade name and original text", async () => {
  vi.spyOn(api, "get").mockResolvedValue([]);
  const start = vi.spyOn(api, "post").mockResolvedValue({ adverse_event_id: "event1", details_complete: false });
  const save = vi.spyOn(api, "put").mockResolvedValue({ adverse_event_id: "event1", details_complete: true });
  render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}><ReactionQuestion tradeName="Трамал" /></QueryClientProvider>);
  await waitFor(() => expect(screen.getByRole("button", { name: "Да" })).toBeEnabled());
  fireEvent.click(screen.getByRole("button", { name: "Да" }));
  await screen.findByLabelText(/Торговое название/);
  expect(start).toHaveBeenCalledTimes(1);
  expect(save).not.toHaveBeenCalled();
  const description = "  Тошнит\nпосле таблетки  ";
  fireEvent.change(screen.getByLabelText("Что произошло?"), { target: { value: description } });
  fireEvent.change(screen.getByLabelText("Когда это началось?"), { target: { value: "Вчера вечером" } });
  fireEvent.click(screen.getByRole("button", { name: "Сохранить сообщение" }));
  await screen.findByText("Сообщение сохранено.");
  expect(save).toHaveBeenCalledWith("/me/reaction-signals/event1", { trade_name: "Трамал", description, onset_text: "Вчера вечером" });
});
