import { defineConfig, devices } from "@playwright/test";

// Config files run in Node; the app tsconfig has no Node types (no @types/node dependency).
declare const process: { env: Record<string, string | undefined> };

// End-to-end tests run against a real backend (`kompas serve`, address in KOMPAS_API) seeded with
// synthetic data by e2e/seed.ts. E2E_BASE_URL is the site: the Vite dev server (default) or the
// FastAPI-served build (then E2E_BASE_URL = KOMPAS_API).
export default defineConfig({
  testDir: "e2e",
  globalSetup: "./e2e/global-setup.ts",
  timeout: 30_000,
  fullyParallel: false,
  use: {
    baseURL: process.env.E2E_BASE_URL ?? "http://127.0.0.1:5173",
    locale: "ru-RU",
    timezoneId: "Europe/Moscow",
    trace: "retain-on-failure",
  },
  projects: [
    { name: "desktop", use: { ...devices["Desktop Chrome"], viewport: { width: 1440, height: 900 } } },
    { name: "phone", use: { ...devices["Pixel 7"] } },
  ],
});
