/**
 * End-to-end: the main doctor and patient flows against a real, seeded backend (see seed.ts).
 * Writes go to Игорь and Дарья; Анна's open alert stays as seeded.
 */
import { expect, test, type Page } from "@playwright/test";
import { API, DOCTOR, PATIENT_PASSWORD } from "./seed";

async function signIn(page: Page, email: string, password: string): Promise<void> {
  await page.goto("/login");
  await page.getByLabel("Электронная почта").fill(email);
  await page.getByLabel("Пароль").fill(password);
  await page.getByRole("button", { name: "Войти" }).click();
}

async function apiLogin(email: string, password: string): Promise<string> {
  const r = await fetch(`${API}/api/v1/auth/login`, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({ email, password }),
  });
  return ((await r.json()) as { access_token: string }).access_token;
}

/** The doctor assigns a one-time PHQ-9 to Игорь today, so he has a questionnaire to fill. */
async function assignQuestionnaire(): Promise<void> {
  const token = await apiLogin(DOCTOR.email, DOCTOR.password);
  const headers = { authorization: `Bearer ${token}`, "content-type": "application/json" };
  const list = (await (await fetch(`${API}/api/v1/overview/patients?limit=200`, { headers })).json()) as {
    items: { full_name: string; case_id: string | null }[];
  };
  const igor = list.items.find((p) => p.full_name === "Белов Игорь");
  if (!igor?.case_id) throw new Error("seed: Игорь has no case");
  const today = new Date(Date.now() + 3 * 3600_000).toISOString().slice(0, 10);
  const r = await fetch(`${API}/api/v1/cases/${igor.case_id}/assignments`, {
    method: "POST",
    headers,
    body: JSON.stringify({ scale_code: "PHQ-9", mode: "once", starts_on: today, due_days: 3 }),
  });
  if (!r.ok) throw new Error(`assignment: ${r.status} ${await r.text()}`);
}

test.describe("doctor", () => {
  test("overview shows the open alert; the patient card opens every tab", async ({ page }) => {
    await signIn(page, DOCTOR.email, DOCTOR.password);
    await expect(page).toHaveURL(/\/d$/);
    await expect(page.getByText("Смирнова Анна").first()).toBeVisible();
    await expect(page.getByText("Срочно").first()).toBeVisible();

    await page.goto("/d/patients");
    const patients = page.getByRole("table", { name: "Пациенты" });
    await expect(patients.getByRole("columnheader")).toHaveText([
      "ФИО", "Дата последнего приёма", "Дата последней активности", "Алёрты",
    ]);
    await page.getByRole("link", { name: /Смирнова Анна/ }).first().click();
    await expect(page.getByRole("heading", { name: "Смирнова Анна" })).toBeVisible();
    for (const tab of ["Профиль безопасности", "Дневник", "Шкалы", "Терапия", "Приёмы и случай", "Сводка"]) {
      await page.locator("nav.tabs").getByRole("link", { name: new RegExp(`^${tab}`) }).click();
      await expect(page.getByRole("alert")).toHaveCount(0);
    }
  });

  test("repeated questionnaire duration defaults to two intervals and steps by one", async ({ page }) => {
    await signIn(page, DOCTOR.email, DOCTOR.password);
    await expect(page).toHaveURL(/\/d$/);
    await page.goto("/d/patients");
    await page.getByRole("link", { name: /Смирнова Анна/ }).first().click();
    await page.locator("nav.tabs").getByRole("link", { name: /^Шкалы/ }).click();
    await page.getByRole("button", { name: "Назначить анкету", exact: true }).click();
    await page.getByLabel("Анкета", { exact: true }).selectOption("PHQ-9");
    const due = page.getByLabel("Срок, дней");
    const save = page.getByRole("button", { name: "Назначить", exact: true });
    await page.getByRole("button", { name: "Повторять", exact: true }).click();
    await expect(due).toHaveValue("28");
    await due.press("ArrowUp");
    await expect(due).toHaveValue("42");
    await due.press("ArrowDown");
    await expect(due).toHaveValue("28");
    await due.fill("15");
    await expect(save).toBeDisabled();
    await page.getByLabel("Каждые, дней").fill("7");
    await expect(due).toHaveValue("14");
    await due.press("ArrowDown");
    await expect(due).toHaveValue("7");
    await due.press("ArrowDown");
    await expect(due).toHaveValue("7");
    await expect(save).toBeEnabled();
    await page.getByLabel("Каждые, дней").fill("");
    await expect(save).toBeDisabled();
    await page.getByRole("button", { name: "Разово", exact: true }).click();
    await expect(due).toHaveValue("3");
    await due.press("ArrowUp");
    await expect(due).toHaveValue("4");
    await expect(save).toBeEnabled();
  });

  test("knowledge base diagnoses link only the sources that exist", async ({ page }) => {
    await signIn(page, DOCTOR.email, DOCTOR.password);
    await expect(page).toHaveURL(/\/d$/);
    await page.goto("/d/kb/diagnoses");
    const f33 = page.getByRole("row", { name: /^F33\.1/ });
    await expect(f33.getByRole("link")).toHaveCount(2);
    await expect(f33.getByRole("link", { name: "Приказ 745н от 24.08.2026" })).toHaveAttribute(
      "href",
      "https://www.consultant.ru/document/cons_doc_LAW_545005/",
    );
    const f411 = page.getByRole("row", { name: /^F41\.1/ });
    await expect(f411.getByRole("link")).toHaveCount(1);
    await expect(f411.getByRole("link")).toHaveAttribute("href", "https://cr.minzdrav.gov.ru/preview-cr/457_3");
  });

  test("a doctor can't open patient pages", async ({ page }) => {
    await signIn(page, DOCTOR.email, DOCTOR.password);
    await expect(page).toHaveURL(/\/d$/);
    await page.goto("/p/meds");
    await expect(page).toHaveURL(/\/d$/);
  });
});

test.describe("patient", () => {
  test("a questionnaire ends with «Ответы сохранены» and no score", async ({ page }) => {
    await assignQuestionnaire();
    await signIn(page, "igor@example.ru", PATIENT_PASSWORD);
    await expect(page).toHaveURL(/\/p$/);
    await page.goto("/p/forms");
    await page.getByRole("link", { name: "Пройти" }).first().click();
    for (let step = 0; step < 40; step += 1) {
      await page.getByRole("radio").first().click();
      const done = page.getByRole("button", { name: "Сохранить результат" });
      if (await done.isVisible()) {
        await done.click();
        break;
      }
      await page.getByRole("button", { name: "Далее" }).click();
    }
    await expect(page.getByText("Ответы сохранены")).toBeVisible();
    await expect(page.locator("body")).not.toContainText(/балл|сумм|тяжест|норма/i);
    await page.getByRole("link", { name: "Вернуться на главную" }).click();
    await expect(page).toHaveURL(/\/p$/);
  });

  test("a diary entry is saved and shows no triage", async ({ page }) => {
    await signIn(page, "darya@example.ru", PATIENT_PASSWORD);
    await page.goto("/p/diary?new=1");
    for (let step = 0; step < 12; step += 1) {
      const save = page.getByRole("button", { name: "Сохранить запись" });
      if (await save.isVisible()) {
        await save.click();
        break;
      }
      await page.getByRole("button", { name: "Дальше" }).click();
    }
    await expect(page.getByText("Запись сохранена")).toBeVisible();
    await expect(page.locator("body")).not.toContainText(/Стабильно|Требует внимания|Срочно/);
  });

  test("a patient can't open doctor pages", async ({ page }) => {
    await signIn(page, "darya@example.ru", PATIENT_PASSWORD);
    await expect(page).toHaveURL(/\/p$/);
    await page.goto("/d/patients");
    await expect(page).toHaveURL(/\/p$/);
  });
});
