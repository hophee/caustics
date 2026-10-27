# Клинический компас — website

The doctor's and the patient's website. It talks to the backend only through `/api/v1`
(`src/backend`); there is no other server.

Stack (D-100, D-112): React 19, react-router, TanStack Query, TypeScript (strict), Vite; vitest +
Testing Library for unit tests, Playwright for end-to-end tests. No UI kit, CSS framework or chart
library: the look comes from the approved canvas boards (`design/canvas/project`).

## Run

```bash
# 1. backend (see src/backend/README.md): Postgres, migrations, a doctor account, API + worker
cd src/backend && uv run kompas serve --port 8000     # and `uv run kompas worker` in a second terminal

# 2. website, dev mode: Vite proxies /api to the API (KOMPAS_API, default http://127.0.0.1:8000)
cd src/frontend
npm install
npm run dev                                           # http://127.0.0.1:5173

# synthetic demo data through the API (four patients, diary, questionnaires, an alert, a prescription)
KOMPAS_DOCTOR_EMAIL=doctor@example.ru KOMPAS_DOCTOR_PASSWORD=… node e2e/seed.ts http://127.0.0.1:8000
```

Production (D-114): `npm run build` writes `dist/`; `kompas serve` serves it at `/` from the same origin
as the API (`KOMPAS_WEB_DIR`, default `src/frontend/dist`).

## Check

```bash
npm run typecheck && npm run lint && npm test         # types, lint, unit tests
KOMPAS_API=http://127.0.0.1:8000 npm run e2e          # end-to-end, against a running API + `npm run dev`
E2E_BASE_URL=http://127.0.0.1:8000 KOMPAS_API=http://127.0.0.1:8000 npm run e2e   # …or against the built site
npm run api:types                                     # after backend API changes: openapi.json → src/api/schema.d.ts
```

`openapi.json` is a copy of the backend's `/openapi.json`; refresh it when the API changes.

## Layout

```
src/api/          HTTP client (ApiError, token), payload types, shared queries + key conventions
src/auth/         session: token in localStorage, /auth/me, sign-in / activation / sign-out
src/live/         SSE notifications: single-use ticket, reconnect with after_seq, query invalidation,
                  doctor's browser confirms alert delivery
src/shell/        doctor layout (/d) and patient layout (/p)
src/screens/      doctor/, patient/, public/ — one file per board
src/ui/           dialog, loading/error states, level pills, icons, screen classes
src/lib/          Russian formatting (dates, ages, plurals)
src/styles/       doctor.css / patient.css (canvas k.css / p.css), screens/*.css (per-board, generated)
e2e/              Playwright tests + seed.ts
tools/            screen_css.py: regenerates src/styles/screens from the canvas boards
```

## Styles from the canvas

Each board is `k.css` / `p.css` plus its own `<style>`. `python3 tools/screen_css.py` copies every
board's own style into `src/styles/screens/<slug>.css`, scoped as `.k.scr-<slug>` (doctor),
`.p.scr-<slug>` (patient) or `.dlg.scr-<slug>` (dialogs). A screen calls `useScreen("<slug>")` and the
shell adds the class to its root, so the approved CSS applies unchanged. Don't edit the generated files;
change the board and regenerate, or add a co-located `<Screen>.css` for app-only states.

## Rules the code keeps

- Patient screens never show scores, ranges, triage, warnings, doctor notes or test results; after a
  questionnaire the patient sees only «Ответы сохранены» (D-109).
- Warnings are never hidden (A05); missing data is shown as «Неизвестно», never as "no risk".
- Blocks without a backend (appointments, documents, demo labs, adverse events) are hidden (D-113).
- Patient writes carry a `submission_id`, so a retry never duplicates an entry.
