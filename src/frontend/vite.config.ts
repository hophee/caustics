/// <reference types="vitest/config" />
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

// Config files run in Node; the app tsconfig has no Node types (no @types/node dependency).
declare const process: { env: Record<string, string | undefined> };

// Dev: the API runs as `kompas serve` on :8000; the dev server proxies /api to it (D-114).
// Prod: `npm run build` writes dist/, which FastAPI serves from the same origin.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: { "/api": { target: process.env.KOMPAS_API ?? "http://127.0.0.1:8000", changeOrigin: false } },
  },
  build: { outDir: "dist", sourcemap: true },
  test: {
    environment: "jsdom",
    setupFiles: ["src/test/setup.ts"],
    include: ["src/**/*.test.{ts,tsx}"],
  },
});
