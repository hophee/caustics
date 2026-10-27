import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { Drugs } from "./Drugs";

function Location() {
  return <output data-testid="location">{useLocation().search}</output>;
}

afterEach(() => vi.unstubAllGlobals());

it("opens the filter, selects only one form, preserves searches in card links, and selects all", async () => {
  const requests: URL[] = [];
  vi.stubGlobal("IntersectionObserver", class {
    observe() {}
    disconnect() {}
  });
  vi.stubGlobal("fetch", vi.fn(async (input: string) => {
    const url = new URL(input, "http://localhost");
    requests.push(url);
    let data: unknown;
    if (url.pathname.endsWith("/forms")) data = ["капли", "таблетки", "капсулы"];
    else if (url.pathname.endsWith("/version")) data = { version: "test" };
    else if (url.pathname.endsWith("/drugs")) data = {
      items: [{ label_id: "a", trade_name: "Золофт", substances: ["сертралин"], form: "таблетки", atc: null }],
      total: 1, offset: 0, limit: 30,
    };
    else data = { trade_name: "Золофт", substances: [], groups: [] };
    return new Response(JSON.stringify(data), { status: 200 });
  }));
  render(
    <QueryClientProvider client={new QueryClient({ defaultOptions: { queries: { retry: false } } })}>
      <MemoryRouter initialEntries={["/d/kb/drugs"]}>
        <Drugs /><Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByLabelText("Общий поиск"), { target: { value: "зол" } });
  fireEvent.change(screen.getByLabelText("Действующее вещество (МНН)"), { target: { value: "серт" } });
  fireEvent.change(screen.getByLabelText("Торговое название"), { target: { value: "золофт" } });
  expect(screen.queryByLabelText("Выбрать всё")).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole("button", { name: "Фильтр" }));
  expect(await screen.findByLabelText("Выбрать всё")).toBeChecked();
  expect(screen.getByLabelText("Таблетки")).not.toBeChecked();
  fireEvent.click(screen.getByLabelText("Таблетки"));
  expect(screen.getByLabelText("Выбрать всё")).not.toBeChecked();
  expect(screen.getByLabelText("Таблетки")).toBeChecked();
  fireEvent.click(screen.getByLabelText("Капли"));
  expect(screen.getByLabelText("Таблетки")).not.toBeChecked();
  expect(screen.getByLabelText("Капли")).toBeChecked();
  await waitFor(() => {
    const last = requests.filter((url) => url.pathname.endsWith("/drugs")).at(-1);
    expect(last?.searchParams.get("q")).toBe("зол");
    expect(last?.searchParams.get("substance")).toBe("серт");
    expect(last?.searchParams.get("trade_name")).toBe("золофт");
    expect(last?.searchParams.getAll("forms")).toEqual(["капли"]);
  });
  fireEvent.click(screen.getByRole("link", { name: /Золофт · таблетки/ }));
  const selected = new URLSearchParams(screen.getByTestId("location").textContent ?? "");
  expect(selected.get("label")).toBe("a");
  expect(selected.get("substance")).toBe("серт");
  expect(selected.getAll("forms")).toEqual(["капли"]);
  fireEvent.click(screen.getByLabelText("Выбрать всё"));
  expect(screen.getByLabelText("Выбрать всё")).toBeChecked();
  expect(screen.getByLabelText("Капли")).not.toBeChecked();
  expect(screen.getByLabelText("Капсулы")).not.toBeChecked();
  expect(new URLSearchParams(screen.getByTestId("location").textContent ?? "").has("forms")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Фильтр" }));
  expect(screen.queryByLabelText("Выбрать всё")).not.toBeInTheDocument();
});
