# kompas — backend of Клинический компас

The domain layer (questionnaire scoring, drug safety checks, guideline-based selection, diary triage,
alert lifecycle), a PostgreSQL persistence layer, a FastAPI HTTP API with live notifications (SSE),
a background worker, and a CLI.

## Setup

```bash
cd src/backend
uv sync
export KOMPAS_OHLP_CSV=/path/to/ohlp_fields_v4.csv   # the big drug CSV (not in git)
uv run kompas --help
```

Without `KOMPAS_OHLP_CSV` (or `--ohlp`), scale and diary commands still work; drug commands (and the
API's basket/suggestions/drug search, which answer 503) need it.

## Run the server

PostgreSQL for development comes from pip (no Docker): `tools/devdb` ships real PostgreSQL binaries.

```bash
# from the repository root: start Postgres (data in .devdb/, gitignored) and print its URL
uv run --project tools/devdb kompas-devdb start
export KOMPAS_DATABASE_URL='postgresql+asyncpg://postgres@/kompas?host=/tmp/kompas-devdb-<uid>'
export KOMPAS_JWT_SECRET='…at least 32 characters…'
export KOMPAS_INVITE_SECRET='…another 32+ characters…'

cd src/backend
uv run alembic upgrade head                                   # create / migrate tables
uv run kompas create-admin admin@example.ru "Админ"              # admin: gives registered accounts the doctor role
uv run kompas create-doctor doctor@example.ru "Иванова И. И."   # or create a doctor directly
uv run kompas serve --port 8000                               # API: http://127.0.0.1:8000/docs
uv run kompas worker                                          # second terminal: alerts, diary model, notifications
```

Patient cards store `last_name`, `first_name`, and `middle_name` separately (60 characters each).
`POST /api/v1/patients` requires surname and given name; patronymic defaults to an empty string.
Responses retain a computed `full_name` for display. Migration `a61d8c2e4f90` splits existing names
in surname–given name–patronymic order, leaving missing parts empty. Values exceeding 60 characters
stop the migration instead of being truncated; review those records before retrying.

The server also serves the website: after `npm run build` in `src/frontend`, open
http://127.0.0.1:8000/ (directory `KOMPAS_WEB_DIR`, default `src/frontend/dist`; see `src/frontend/README.md`).

Accounts (D-088): anyone registers (`POST /api/v1/auth/register`) and starts **without a role**; an admin
makes them a doctor (`/admin/users/{id}/doctor` or `kompas grant-doctor <email>`). Main flow: the doctor creates
a patient (`POST /api/v1/patients` → one-time invite code), the person enters the code in their account
(`POST /api/v1/auth/link`; or registers and links in one step with `POST /api/v1/auth/activate`), the doctor
opens a case and sets the main diagnosis. With `KOMPAS_DEMO_MODE=true`, `POST /api/v1/demo/patient` and
`/demo/doctor` create throwaway synthetic accounts. Patients fill scales (`/me/responses`) and the diary (`/me/diary`); critical answers raise
alerts that the worker delivers to the doctor as live notifications (`/me/notifications/stream`, opened
with a single-use ticket from `/me/notifications/ticket`). The basket: `GET /patients/{id}/therapy/current`
→ `POST …/basket/check` on every change → `POST …/basket/confirm` with `base_token` (422
`justification_required` drives the second confirmation; 409 means the therapy changed meanwhile).

Therapy follow-up: the patient sees today's doses (`GET /me/doses`) and marks them «Принял / Пропустил»
(`POST /me/doses/marks`, a skip needs a reason); the worker sends dose reminders by the patient's rule
(`/me/reminders`) and signals the doctor after 3 days of skipped/unmarked doses; the doctor sees the
calendar and adherence (`GET /patients/{id}/adherence`). There are no doctor ↔ patient messages (D-120).
Referrals: the doctor
records or reviews recommendations «обязательно / желательно», decides with a reason and issues the
referral (`/patients/{id}/referrals`); the patient sees issued ones (`/me/referrals`). Adverse events: the patient reports a side effect (`/me/adverse-events`, a severe one alerts the doctor); the doctor confirms or dismisses it and flags a Росздравнадзор report (`/patients/{id}/adverse-events`). Appointments: the doctor plans visits (`/patients/{id}/appointments`, own schedule `/appointments/schedule`), moves/cancels/completes them (`/appointments/{id}`…); the patient sees them (`/me/appointments`) and asks to reschedule or cancel (`/me/appointments/{id}/change-requests`), the doctor decides (`/appointments/requests`, `/appointment-requests/{id}/decision`); the worker reminds the patient a day and 2 h before. Documents: the patient uploads PDF/photos (`POST /me/documents`, multipart), marked «не подтверждено» until the doctor reviews them (`/documents/todo`, `/documents/{id}/review`); the doctor issues a visit summary / выписка (`POST /patients/{id}/documents`) or a prescription document from a confirmed version (`/patients/{id}/documents/prescription`); files at `/documents/{id}/file` (patient and treating doctor only). Nothing is extracted from documents automatically. Demo labs (A13, doctor-only, always flagged demo): `/patients/{id}/demo-labs` (tests in `/demo-labs/options`); the basket checks use fresh values per `knowledge_data/rules/labs.yaml`. «Вы часто назначаете»: `GET /therapy/frequent?patient_id=` (the doctor's most frequent new picks, with КР status for the patient).

Patients never receive scores, ranges, item answers, triage levels, warnings or doctor notes.

Diary and message texts are also scored by a local language model in the worker (default: translated RU→EN locally by opus-mt-ru-en, then Qwen3-4B with the English few-shot prompt, on CPU,
~16 GB RAM, weights from the Hugging Face cache; set `HF_HUB_OFFLINE=1` in production). The model can
only raise the triage level. `KOMPAS_TEXT_MODEL=keywords` turns it off; `KOMPAS_TEXT_MODEL_ID` /
`KOMPAS_TEXT_MODEL_PROMPT=few_shot` (Russian, no translation) switch model or prompt (see D-075/D-076).

## Try the domain logic without a server

```bash
# questionnaires: what the engine computes, what the doctor sees, what the patient sees
uv run kompas scales list
uv run kompas scales score PHQ-9 --answers 2,2,3,2,2,2,2,3,1
uv run kompas scales validate            # checks every knowledge file

# drugs and guideline suggestions
uv run kompas drugs search сертралин
uv run kompas select F33.2                # every КР row of the diagnosis with its drugs

# the doctor's basket: box colours and hover texts, then confirmation
uv run kompas basket check examples/basket_anna.json
uv run kompas basket confirm examples/basket_anna.json          # refused: red warnings need justification
uv run kompas basket confirm examples/basket_anna.json --justify <id>="обоснование" --justify <id>="..."

# diary triage
uv run kompas triage entry examples/diary_entry.json
uv run kompas triage text "Мыслей о самоубийстве нет, просто устаю"   # shows why keywords alone aren't enough
```

Add `--json` before the command for machine-readable output.

## Layout

```
knowledge_data/              clinical knowledge as files (versioned, loaded by load_knowledge)
  scales/*.yaml              one file per questionnaire: items, scoring method, bands, patient texts
  complaints.yaml            diary complaints (8 categories, 36 complaints)
  rules/checks.yaml          drug-check policy: severity matrix, conditions, group load, vocabulary
  rules/interaction_overrides.yaml   clinically reviewed severities for drug pairs
  rules/triage.yaml          diary triage rules and keyword lists
  kr/*.json                  КР table (211 rows), drug classes, role rules, restrictions, 21 diagnoses
                             with their КР documents (guidelines.json) and care standards (standards.json)
migrations/                  Alembic migrations (URL from KOMPAS_DATABASE_URL)
src/kompas/core/             errors, clock, ICD codes, config, db, security, outbox
src/kompas/modules/<module>/
  domain/                    pure logic (no database)
  models.py                  tables
  service.py                 use cases: access check → domain → store → audit, one transaction
  router.py                  HTTP endpoints under /api/v1
  modules: identity, audit, cases, assessments, diary, therapy, monitoring, notifications, knowledge
src/kompas/api/              app factory (error mapping) and shared dependencies
src/kompas/worker.py         outbox worker (alerts, diary model, prescription notifications)
src/kompas/json_input.py     JSON → domain conversion (CLI files and API payloads)
src/kompas/cli.py            command-line interface (incl. serve / worker / create-doctor)
examples/                    sample basket and diary files (fictional patient)
tests/                       pytest; tests/api runs against a real PostgreSQL (devdb or KOMPAS_TEST_DATABASE_URL)
```

## Checks

```bash
uv run pytest            # starts the dev Postgres for tests/api if KOMPAS_TEST_DATABASE_URL is unset
uv run ruff check src tests
uv run mypy src
```
