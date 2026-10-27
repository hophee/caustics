import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, describe, expect, it, vi } from "vitest";
import { api } from "../api/client";
import { EmergencyContacts } from "./EmergencyContacts";

const contact = { last_name: "Иванов", first_name: "Иван", middle_name: "", role: "Брат", phone: "89991234567" };

function show(patientId?: string) {
  return render(<QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
    <EmergencyContacts patientId={patientId} />
  </QueryClientProvider>);
}

afterEach(() => vi.restoreAllMocks());

describe("emergency contacts dialog", () => {
  it("disables the doctor's button when no contacts exist", async () => {
    const get = vi.spyOn(api, "get").mockResolvedValue([]);
    show("patient-id");
    await waitFor(() => expect(get).toHaveBeenCalledWith("/patients/patient-id/emergency-contacts"));
    expect(screen.getByRole("button", { name: "Экстренные контакты" })).toBeDisabled();
  });

  it("shows the doctor a readable phone link without a form", async () => {
    vi.spyOn(api, "get").mockResolvedValue([contact]);
    show("patient-id");
    const button = screen.getByRole("button", { name: "Экстренные контакты" });
    await waitFor(() => expect(button).toBeEnabled());
    fireEvent.click(button);
    expect(screen.getByText("Иванов Иван")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "8 (999) 123-45-67" })).toHaveAttribute("href", "tel:+79991234567");
    expect(screen.queryByRole("button", { name: "Добавить экстренный контакт" })).not.toBeInTheDocument();
  });

  it("stops offering a sixth contact", async () => {
    vi.spyOn(api, "get").mockResolvedValue(Array.from({ length: 5 }, () => contact));
    show();
    fireEvent.click(screen.getByRole("button", { name: "Экстренные контакты" }));
    expect(await screen.findByRole("button", { name: "Добавить экстренный контакт" })).toBeDisabled();
  });

  it("saves the patient's contact and returns to the list", async () => {
    vi.spyOn(api, "get").mockResolvedValue([]);
    const post = vi.spyOn(api, "post").mockResolvedValue([contact]);
    show();
    fireEvent.click(screen.getByRole("button", { name: "Экстренные контакты" }));
    fireEvent.click(await screen.findByRole("button", { name: "Добавить экстренный контакт" }));
    fireEvent.change(screen.getByLabelText("Фамилия"), { target: { value: "Иванов" } });
    fireEvent.change(screen.getByLabelText("Имя"), { target: { value: "Иван" } });
    fireEvent.change(screen.getByLabelText("Кем вам приходится?"), { target: { value: "Брат" } });
    fireEvent.change(screen.getByLabelText("Номер телефона"), { target: { value: "89991234567" } });
    fireEvent.click(screen.getByRole("button", { name: "Сохранить" }));
    await waitFor(() => expect(post).toHaveBeenCalledWith("/me/emergency-contacts", contact));
    expect(await screen.findByText("Иванов Иван")).toBeInTheDocument();
  });
});
