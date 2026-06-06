# Healthcare Appointment No-Show Analytics

A data-engineering service that ingests a denormalised healthcare appointment
CSV into a **normalised PostgreSQL** schema and exposes **no-show analytics** over
a REST API. Built for the Horatio Data Engineering challenge.

- **Section 1 (REST API)** — CSV bulk load + transactional batch insert.
- **Section 2 (SQL Analytics)** — endpoints 4.1 and 4.2.
- **Bonus** — Docker, automated tests, structured logging, OpenAPI docs, CI.

---

## Table of contents

- [Stack & rationale](#stack--rationale)
- [Architecture](#architecture)
- [Data model](#data-model)
- [Source → schema mapping](#source--schema-mapping)
- [Quick start (Docker)](#quick-start-docker)
- [Local development](#local-development)
- [API reference](#api-reference)
- [Analysis year](#analysis-year)
- [Data governance & edge cases](#data-governance--edge-cases)
- [Testing](#testing)
- [Project structure](#project-structure)
- [Cloud deployment notes](#cloud-deployment-notes)

---

## Stack & rationale

| Concern | Choice | Why |
| --- | --- | --- |
| Language | **Python 3.12** | Ubiquitous for data engineering; rich ecosystem. |
| Web framework | **FastAPI** | Async, typed, automatic OpenAPI/Swagger, first-class Pydantic integration. |
| Validation | **Pydantic v2** | Declarative request/response validation; v2 is fast and strict. |
| DB access | **SQLAlchemy Core** (not ORM) | This is an ETL/analytics workload, not a CRUD domain model. Core gives explicit, set-based SQL and bulk `INSERT ... ON CONFLICT` without ORM overhead or session/identity-map surprises. |
| Database | **PostgreSQL 16** | `INSERT ... ON CONFLICT` (upserts), aggregate `FILTER`, `EXTRACT`, partial indexes — exactly what the dedup loader and analytical queries need. |
| Migrations | **Alembic** | Versioned, reviewable schema; the model metadata is the single source of truth (`alembic check` enforces no drift). |
| Config | **pydantic-settings** | 12-factor config from env/`.env`, typed and validated. |
| Containers | **Docker + Compose** | One-command reproducible stack (api + db). |
| Tests | **pytest + httpx** (FastAPI `TestClient`) | Unit (parsing) + integration (HTTP against real Postgres). |

**Why SQLAlchemy Core over the ORM?** The job is moving rows, deduplicating
them, and running analytical aggregates. Core lets us express dialect-aware bulk
upserts (`pg_insert(...).on_conflict_do_update(...)`) and parameterised
analytical SQL directly, which is clearer and faster than mapping these onto ORM
objects.

---

## Architecture

```mermaid
flowchart LR
    ops[Operations team / upstream systems]
    subgraph API["FastAPI service"]
        direction TB
        mw[Request-ID middleware<br/>+ JSON logging]
        routes[Routers:<br/>ingest · analytics · health]
        svc[Services:<br/>parsing · ingest · batch · analytics]
        mw --> routes --> svc
    end
    pg[(PostgreSQL<br/>neighbourhoods · patients · appointments)]

    ops -- "POST /ingest/csv (file)" --> API
    ops -- "POST /appointments/batch (JSON)" --> API
    ops -- "GET /analytics/*" --> API
    svc -- "SQLAlchemy Core" --> pg
```

**Layering / separation of concerns**

- `app/api` — HTTP surface only (routing, status codes, dependency wiring).
- `app/services` — business logic. `parsing.py` is pure (no DB) and fully unit-tested.
- `app/db` — schema (SQLAlchemy Core metadata), engine, Alembic migrations.
- `app/schemas` — Pydantic request/response contracts.
- `app/core` — config and structured logging.

---

## Data model

The source is a single **denormalised** CSV (one row per appointment, with
patient and neighbourhood attributes repeated inline). It is normalised into
three tables to remove that redundancy:

```
neighbourhoods 1───< appointments >───1 patients
```

### `neighbourhoods` (lookup)
Surrogate key, populated on ingest. One row per distinct neighbourhood name.

| Column | Type | Notes |
| --- | --- | --- |
| `neighbourhood_id` | `SERIAL` PK | surrogate key |
| `name` | `VARCHAR(120)` | `NOT NULL`, `UNIQUE` |

### `patients` (one row per patient, deduplicated)

| Column | Type | Notes |
| --- | --- | --- |
| `patient_id` | `BIGINT` PK | natural key from the CSV |
| `gender` | `CHAR(1)` | `CHECK IN ('M','F','O')` (M / F / Other) |
| `year_of_birth` | `SMALLINT` | derived: `appointment_year − age` |
| `scholarship` | `BOOLEAN` | default `false` |
| `hypertension` | `BOOLEAN` | default `false` |
| `diabetes` | `BOOLEAN` | default `false` |
| `alcoholism` | `BOOLEAN` | default `false` |
| `handicap` | `SMALLINT` | `CHECK BETWEEN 0 AND 4` |
| `created_at` / `updated_at` | `TIMESTAMPTZ` | audit columns |

### `appointments` (fact table, grows over time)

| Column | Type | Notes |
| --- | --- | --- |
| `appointment_id` | `BIGINT` PK | natural key from the CSV |
| `patient_id` | `BIGINT` FK → patients | `NOT NULL` |
| `neighbourhood_id` | `INT` FK → neighbourhoods | `NOT NULL` |
| `scheduled_at` | `TIMESTAMPTZ` | when the booking was made |
| `appointment_at` | `TIMESTAMPTZ` | when the visit is/was scheduled |
| `age_at_appointment` | `SMALLINT` | `CHECK >= 0` |
| `sms_received` | `BOOLEAN` | default `false` |
| `no_show` | `BOOLEAN` | outcome |
| `created_at` | `TIMESTAMPTZ` | audit column |

**Integrity constraint:** `CHECK (appointment_at >= scheduled_at::DATE)` — a visit
cannot fall on a calendar day before the day it was booked.

**Indexes (supporting the analytical queries):**

- `idx_appt_appointment_at`, `idx_appt_neighbourhood_id`, `idx_appt_patient_id`
- `idx_appt_no_show` — **partial** index `WHERE no_show = TRUE` (both queries filter on it)
- `idx_appt_analytics` — composite `(neighbourhood_id, appointment_at, no_show)` for the grouped aggregates
- `idx_neighbourhoods_name` — name lookups during dedup

> Why natural keys for `patients`/`appointments` but a surrogate for
> `neighbourhoods`? The CSV provides stable unique ids for patients and
> appointments (ideal for idempotent upserts), whereas neighbourhood is free
> text — a surrogate key keeps the fact table narrow and rename-safe.

---

## Source → schema mapping

The loader accepts **two header dialects** and normalises both:

- the brief's original Kaggle columns (`PatientId`, `AppointmentID`, `Hipertension`, `Handcap`, `No-show` = `Yes`/`No`), and
- the richer synthetic export actually provided (`patient_id` = `PAT-25795`, `appointment_id` = `APT-100000`, `gender` = `Female`/`Male`, `neighborhood`, `disability`, `no_show` = `0`/`1`, plus many extra columns that are ignored).

| Target | Derived from CSV | Transformation |
| --- | --- | --- |
| `patient_id` / `appointment_id` | `patient_id` / `appointment_id` | strip non-digits (`PAT-25795` → `25795`) |
| `gender` | `gender` | first letter upper → `M`/`F`/`O` (`Female` → `F`, `Other` → `O`) |
| `year_of_birth` | `age` + `appointment_day` | `appointment_year − age` |
| `neighbourhood.name` | `neighbourhood`/`neighborhood` | trimmed; deduplicated |
| `scheduled_at` | `scheduled_day` | ISO date/datetime (UTC) |
| `appointment_at` | `appointment_day` (+ `appointment_hour`) | hour layered onto a bare date |
| `age_at_appointment` | `age` | int, range-checked `[0,120]` |
| `handicap` | `handicap`/`handcap`/`disability` | int, range-checked `[0,4]` |
| booleans | `0/1`, `true/false`, `yes/no` | tolerant parsing |

Header matching is case-insensitive and ignores spaces/underscores/hyphens.

---

## Quick start (Docker)

Requires Docker + Docker Compose.

```bash
cp .env.example .env          # optional; compose has sensible defaults
docker compose up --build
```

This starts PostgreSQL, **runs the Alembic migrations automatically**
(see `docker-entrypoint.sh`), and serves the API on `http://localhost:8000`.

```bash
# Load the bundled sample
curl -X POST http://localhost:8000/ingest/csv -F "file=@sample_data.csv"

# Analytics
curl "http://localhost:8000/analytics/no-shows-by-quarter?year=2024"
curl "http://localhost:8000/analytics/above-average-neighbourhoods?year=2024"
```

Interactive docs: **http://localhost:8000/docs** (Swagger) and `/redoc`.

---

## Local development

Requires Python 3.11+ and a reachable PostgreSQL.

```bash
python -m venv .venv && source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# Point at your database (or use .env)
export DATABASE_URL=postgresql+psycopg2://noshow:noshow@localhost:5432/noshow

alembic upgrade head
uvicorn app.main:app --reload
```

---

## API reference

### `POST /ingest/csv`
Multipart upload (`file`) of the historical CSV. Deduplicates patients and
neighbourhoods via `INSERT ... ON CONFLICT`; idempotent on re-upload.

```json
{
  "rows_received": 8, "rows_valid": 8, "rows_invalid": 0,
  "neighbourhoods_created": 2, "patients_inserted": 5, "patients_updated": 0,
  "appointments_inserted": 8, "appointments_skipped": 0, "errors": []
}
```

### `POST /appointments/batch`
Insert **1–1000** appointments in a **single transaction**. Validates payload
(Pydantic) and referential integrity + duplicates (service). Atomic: if any row
is invalid, **nothing** is written and the response is `422` with row-level
diagnostics.

```json
// request
{ "appointments": [
  { "appointment_id": 900100, "patient_id": 25795, "neighbourhood_id": 1,
    "scheduled_at": "2024-07-01T09:00:00Z", "appointment_at": "2024-07-05T09:00:00Z",
    "age_at_appointment": 42, "sms_received": true, "no_show": false } ] }

// 200 response
{ "inserted": 1, "received": 1, "errors": [] }

// 422 response (bad reference) — nothing inserted
{ "inserted": 0, "received": 1,
  "errors": [ { "index": 0, "appointment_id": 900101,
                "error": "patient_id 999999 does not exist" } ] }
```

### `GET /analytics/no-shows-by-quarter?year=YYYY` — req. 4.1
No-shows per neighbourhood and gender, pivoted by quarter of `appointment_at`,
ordered by neighbourhood then gender.

```json
{ "year": 2024, "rows": [
  { "neighbourhood": "East", "gender": "F", "Q1": 1, "Q2": 1, "Q3": 0, "Q4": 0 },
  { "neighbourhood": "East", "gender": "M", "Q1": 0, "Q2": 0, "Q3": 1, "Q4": 1 },
  { "neighbourhood": "West", "gender": "F", "Q1": 1, "Q2": 0, "Q3": 0, "Q4": 0 },
  { "neighbourhood": "West", "gender": "M", "Q1": 0, "Q2": 0, "Q3": 1, "Q4": 0 } ] }
```

### `GET /analytics/above-average-neighbourhoods?year=YYYY` — req. 4.2
Neighbourhoods whose no-show count exceeds the mean across all neighbourhoods,
ordered by count descending.

```json
{ "year": 2024, "mean_no_shows": 3.0,
  "rows": [ { "id": 1, "neighbourhood": "East", "no_shows": 4 } ] }
```

### `GET /health` · `GET /health/db`
Liveness and readiness (the latter checks DB connectivity).

> Both analytical queries are **parameterised** (`:year` bind parameter, never
> string-interpolated) per SonarSource RSPEC-2077.

---

## Analysis year

The analysis year is **`2024`** (the most recent full calendar year in the
dataset). It is **parameterised** on both analytics endpoints via `?year=YYYY`;
when omitted it falls back to `ANALYSIS_DEFAULT_YEAR` (default `2024`).

---

## Data governance & edge cases

- **Dirty rows are reported, not silently dropped.** CSV ingest validates each
  row independently; invalid rows are skipped and returned in `errors`
  (with the source line number) while valid rows still load. Counts of
  received/valid/invalid are always returned.
- **Validations:** numeric id extraction, gender domain, age `[0,120]`,
  handicap `[0,4]`, date parsing, and `appointment_day >= scheduled_day`.
- **Deduplication:** neighbourhoods (`ON CONFLICT (name) DO NOTHING`),
  patients (`ON CONFLICT (patient_id) DO UPDATE` — latest attributes win),
  appointments (`ON CONFLICT (appointment_id) DO NOTHING` — idempotent reload).
- **Transactional integrity:** each CSV load and each batch insert runs in a
  single transaction; failures leave the database untouched.
- **Observability:** structured JSON logs with a per-request `request_id`
  (honoured from inbound `X-Request-ID` or generated) and request
  method/path/status/duration.

---

## Testing

Unit tests (parsing) need no database. Integration tests run against PostgreSQL
via `TEST_DATABASE_URL` and are skipped if no database is reachable.

```bash
# 1. a database for the integration tests
docker compose up -d db

# 2. run
export TEST_DATABASE_URL=postgresql+psycopg2://noshow:noshow@localhost:5432/noshow
pytest -v
```

- `tests/unit` — pure parsing/normalisation rules.
- `tests/integration` — HTTP tests for ingest (happy path, idempotency, dirty
  rows, bad input), batch (happy path, atomic rejection, in-batch duplicates,
  validation), and analytics (4.1/4.2, year filtering, defaults).

CI (GitHub Actions, `.github/workflows/ci.yml`) spins up Postgres, lints with
ruff, applies migrations, and runs the full suite on every push/PR.

---

## Project structure

```
app/
  api/            routes (ingest, analytics, health) + deps
  core/           config (pydantic-settings) + structured logging
  db/             SQLAlchemy Core models, engine, Alembic migrations
  schemas/        Pydantic request/response models
  services/       parsing, ingest, batch, analytics (business logic)
  main.py         app factory + request-id middleware
tests/            unit + integration
docker-compose.yml · Dockerfile · alembic.ini · sample_data.csv
```

---

## Cloud deployment (Azure)

The image is provider-agnostic, but a ready-to-use Azure pipeline is included.

### Architecture

```mermaid
flowchart LR
    gh[GitHub Actions<br/>deploy.yml] -->|1. test gate| gh
    gh -->|2. build + push| acr[(Azure Container Registry)]
    gh -->|3. set image| app[Azure App Service<br/>Web App for Containers]
    acr -->|managed-identity pull| app
    app -->|sslmode=require| pg[(Azure Database for<br/>PostgreSQL Flexible Server)]
```

### Pipeline — `.github/workflows/deploy.yml`

Runs on every push to `main` as three gated jobs:

1. **test** — full suite against a PostgreSQL service + `ruff`. **If it fails the
   run stops here** — nothing is built or deployed.
2. **build-and-push** — builds the Docker image and pushes it to **Azure
   Container Registry** (tagged with the commit SHA and `latest`).
3. **deploy** — points the **Azure App Service** web app at the new image, then
   smoke-tests `GET /health`. The container entrypoint runs `alembic upgrade head`
   on start, so the schema migrates automatically on each release.

### One-time setup

Provision the Azure resources and the GitHub OIDC identity (after `az login`):

```bash
bash infra/azure-provision.sh                 # provision + print the creds
bash infra/azure-provision.sh --push-github   # also set the GitHub secrets/vars (needs gh)
```

The script is **idempotent** — safe to re-run. It auto-detects the GitHub repo
from the `origin` remote, derives deterministic resource names from your
subscription + resource group, and caches the generated PostgreSQL password at
`infra/.pg-password` so `DATABASE_URL` stays stable across runs. Override any
default via env vars (e.g. `LOCATION=westeurope ACR_NAME=myacr bash infra/azure-provision.sh`).

It creates the resource group, ACR, PostgreSQL Flexible Server, App Service plan
+ Web App (with `WEBSITES_PORT=8000`, `DATABASE_URL` incl. `sslmode=require`, and
AcrPull via managed identity), and a federated GitHub identity with **two**
credentials — one for the build job (`ref:refs/heads/main`) and one for the
deploy job (`environment:production`). It prints the values to add to **GitHub →
Settings → Secrets and variables → Actions** (or sets them for you with
`--push-github`):

| Type | Name | Purpose |
| --- | --- | --- |
| Secret | `AZURE_CLIENT_ID` | OIDC app registration (client) id |
| Secret | `AZURE_TENANT_ID` | Azure AD tenant id |
| Secret | `AZURE_SUBSCRIPTION_ID` | target subscription |
| Variable | `ACR_NAME` | registry name (e.g. `noshowacr123`) |
| Variable | `ACR_LOGIN_SERVER` | e.g. `noshowacr123.azurecr.io` |
| Variable | `AZURE_WEBAPP_NAME` | the Web App name |

Authentication uses **OIDC federated credentials** (no long-lived secrets/
passwords stored in GitHub). Push to `main` to trigger the pipeline; the live API
is then at `https://<AZURE_WEBAPP_NAME>.azurewebsites.net` (`/docs` for Swagger).

> **Other targets:** the same image runs unchanged on Azure Container Apps, AWS
> ECS/Fargate, or GCP Cloud Run — point them at the ACR image and set
> `DATABASE_URL`, `LOG_LEVEL`, and the listen port (`8000`). Structured JSON logs
> to stdout are ingested by Azure Monitor / CloudWatch / Cloud Logging directly.
```
