# Клинический компас — run it locally

A clinical decision-support web app for anxiety and depression (doctor and patient roles), team
«Каустики». This folder runs the whole system on one computer with Docker: the website, the API, a
background worker and a PostgreSQL database. **All data is synthetic** — never enter real patient data.

## What you need

- **Docker** with **Docker Compose v2** (`docker compose version` works):
  Docker Desktop on Windows/macOS, Docker Engine on Linux.
- A 64-bit **x86 (Intel/AMD)** machine. Apple Silicon (M1–M4) runs it through emulation (slower).
- ~4 GB free disk (~12 GB with the language model), 4 GB RAM (**~20 GB RAM with the language model**).
- No internet needed: the images are in `images/`.

## Contents

| Path | What |
|---|---|
| `start.sh` | loads the images, creates `.env` with random secrets, starts everything, creates a doctor |
| `seed-demo.sh` | creates the fixed demo accounts (two doctors, two patients) |
| `compose.yaml` | the services: `db`, `migrate`, `api` (also serves the website), `worker` |
| `images/` | Docker images: the app (`kompas-backend`) and `postgres:16-alpine` |
| `data/ohlp_fields_v4.csv` | the drug-label (ОХЛП) database used by drug search and the safety checks |
| `hf/hub/` | *(only in the "with model" bundle)* local diary text model weights |
| `.env.example` | all settings, for manual setup |
| `VERSION` | the git commit the bundle was built from |

## Start (Linux, macOS, Windows with WSL or Git Bash)

```bash
unzip kompas-bundle-*.zip
cd kompas-bundle-*/
./start.sh
```

The first start takes a few minutes (loading the images). When it prints **Ready**, open
**http://localhost:8080/login** and sign in as the doctor:

- email `doctor@kompas-demo.ru`, password `demo-password-1`

Another port: `KOMPAS_WEB_PORT=9000 ./start.sh` on the first start (later: edit `.env`).

### Windows without a bash shell (PowerShell)

```powershell
Get-ChildItem images\*.tar.gz | ForEach-Object { docker load -i $_.FullName }
Copy-Item .env.example .env   # then open .env and fill in the three secrets (any 32+ random characters)
docker compose up -d --wait
docker compose exec api kompas create-doctor doctor@kompas-demo.ru "Демо Врач" --password demo-password-1
```

## Try it

**Fastest:** on the login page press **«Попробовать как пациент»** or **«Попробовать как врач»** — each
click creates a new throwaway synthetic account with a week of data (the doctor gets three patients,
one with a live alert). Turn the buttons off with `KOMPAS_DEMO_MODE=false` in `.env`.

**Fixed demo accounts:** `./seed-demo.sh` creates two doctors and two patients:

| Who | Email | Password |
|---|---|---|
| Doctor | `demo-doctor@kompas-demo.ru` | `demo-password-1` |
| Doctor | `other-doctor@kompas-demo.ru` | `demo-password-2` |
| Patient | `anna@kompas-demo.ru` | `anna-password-1` |
| Patient | `maria@kompas-demo.ru` | `maria-password-1` |

**Accounts work like this:** anyone can register at **http://localhost:8080/register**; a new account has
no role. It becomes a **patient** by entering a doctor's invite code, or a **doctor** when an admin gives
the role — on the admin page (create the admin with
`docker compose exec api kompas create-admin admin@example.ru "Админ" --password <10+ chars>`, then sign in)
or directly: `docker compose exec api kompas grant-doctor <email>`.

**By hand:**

1. **Doctor:** «Пациенты» → add a patient. The app shows a one-time **invite code**.
2. **Patient:** open a private/incognito window → register at **http://localhost:8080/register** and enter
   the code (or **/activate**: code, email and password in one step). You are now the patient.
3. **Doctor:** open the patient's card: fill in the safety profile, set a diagnosis (e.g. F33.2),
   pick a drug in «Терапия» — the safety checks appear live — and confirm the prescription.
4. **Patient:** sees the prescription and today's doses, writes a diary entry. A worrying entry
   (e.g. «не хочу жить») raises an alert for the doctor within seconds.

More doctors: `docker compose exec api kompas create-doctor <email> "<Full Name>" --password <password>`.
API documentation: **http://localhost:8080/docs**.

## Diary text model

The worker triages diary entries. Two modes (`KOMPAS_TEXT_MODEL` in `.env`):

- `keywords` (default in the light bundle): keyword rules only, no extra RAM.
- `llm`: a local language model (Qwen3-4B + an RU→EN translator), runs on the CPU, nothing leaves the
  computer. Needs the "with model" bundle (`hf/hub/`) and ~20 GB RAM. `start.sh` picks `llm`
  automatically when the weights are present.

After changing `.env`: `docker compose up -d`.

## Everyday commands (run inside this folder)

| What | Command |
|---|---|
| stop (data kept) | `docker compose stop` |
| start again | `./start.sh` or `docker compose up -d` |
| logs | `docker compose logs -f api worker` |
| status | `docker compose ps` |
| remove everything incl. the database | `docker compose down -v` |

If you delete `.env`, also run `docker compose down -v` before starting again (the database keeps the
old password).

## Open it to other computers on your network

Set `KOMPAS_WEB_BIND=0.0.0.0` in `.env` and run `docker compose up -d`. Others open
`http://<your-ip>:8080`. This is a demo with synthetic data — do not expose it to the internet.

## Troubleshooting

- **`permission denied ... docker.sock`** (Linux): add yourself to the docker group
  (`sudo usermod -aG docker $USER`, then log out and in).
- **Port 8080 is busy:** set another `KOMPAS_WEB_PORT` in `.env`, then `docker compose up -d`.
- **Worker restarts with the `llm` model:** not enough RAM — set `KOMPAS_TEXT_MODEL=keywords`.
- **Drug search is empty:** `data/ohlp_fields_v4.csv` is missing or `KOMPAS_OHLP_HOST_PATH` points
  to a wrong file.
