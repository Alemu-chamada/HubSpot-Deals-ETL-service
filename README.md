# HubSpot Deals ETL — Data Integration Service

A Flask + DLT service that extracts **HubSpot CRM Deals** into **PostgreSQL**, runs asynchronous extraction scans with checkpoint/resume, and serves the extracted data through a REST API with Swagger documentation.

[Data Load Tool (DLT)](https://dlthub.com). It manages asynchronous extraction scans, persists checkpoints for crash recovery, and serves completed results back through a REST API.

Built with **Python 3.11**, **Flask / Flask-RESTX**, **DLT + PostgreSQL**, **SQLAlchemy**, **Redis**, and **Docker Compose**. Scan credentials are encrypted at rest with `cryptography`.

## Overview

The service exposes a Flask-RESTX API. A scan request supplies a HubSpot Private App token plus filters; the `ExtractionService` orchestrates a DLT pipeline that calls the HubSpot CRM v3 Deals endpoint (`GET https://api.hubapi.com/crm/v3/objects/deals`), pages with `after` cursors, normalizes each deal, and loads it into an organization-scoped PostgreSQL schema. Jobs and checkpoints are persisted so scans can pause, cancel, and resume.

## Key features

- HubSpot CRM v3 Deals integration (`/crm/v3/objects/deals`)
- Asynchronous scan orchestration: start / status / pause / cancel / remove / list / statistics
- Cursor pagination via `paging.next.after`
- Forwards requested `properties`; maps `includeArchived` → `archived`; clamps `limit` to 1–100
- Rate-limit (HTTP 429) backoff honoring `Retry-After` / `X-RateLimit-Reset`
- HubSpot-specific error handling (`HubSpotAPIError`)
- DLT → PostgreSQL destination with organization-scoped schemas
- Checkpointing & resume (every N pages, and on pause/cancel/error/completion)
- Encrypted storage of scan credentials (cryptography / Fernet)
- Swagger UI at `/docs` and OpenAPI JSON at `/api/swagger.json`
- Multi-environment Docker Compose (dev/stage/prod) with PostgreSQL and Redis
- JSON-structured logging (Loki optional)

## Architecture / data flow

```text
Client ─► Flask-RESTX API ─► ExtractionService ─► HubSpotAPIService ─► HubSpot CRM v3 Deals
                                   │                         │
                                   │                         └─ Bearer token, retry, rate-limit backoff
                                   └─ DLT ──► PostgreSQL (organization dataset)
                                   └─ jobs / checkpoints ──► PostgreSQL
```

## Technology stack

| Area | Technology |
| --- | --- |
| Language | Python 3.11 |
| API | Flask, Flask-RESTX, Flask-CORS |
| Data loading | DLT (`dlt[postgres]`) |
| Database | PostgreSQL (psycopg2 + asyncpg), SQLAlchemy |
| Cache / sessions | Redis |
| Secrets at rest | `cryptography` (Fernet) for stored scan credentials |
| Server | Gunicorn (WSGI) |
| Containers | Docker, Docker Compose |

## Project structure

```text
api/                  Flask-RESTX routes, validation, Swagger models
services/             HubSpot client, DLT source, extraction & job orchestration
models/               Job and checkpoint persistence (SQLAlchemy)
docs/                 Integration, database, and API documentation
test-results/         Sanitized assessment evidence
app.py                Flask application factory
config.py             Application configuration
encrypter.py          Credential encryption helper
loki_logger.py        JSON logging setup (Loki optional)
utils.py              Shared helpers
docker-compose.yml    Multi-environment orchestration (dev/stage/prod)
Dockerfile.*          Dev / stage / prod / test images
requirements.txt      Python dependencies
.env.example          Environment template (placeholders only)
```
## Prerequisites

- Docker Engine with Docker Compose v2 (recommended), or Python 3.11 for local development
- A HubSpot Private App token with the `crm.objects.deals.read` scope
- PostgreSQL 15+ and Redis 7 (or use the bundled Docker Compose services)

## Environment configuration

Copy the safe example and fill in local values:

```bash
cp .env.example .env
```

`.env` is git-ignored; never commit it. The application reads secrets from environment variables and from the per-scan request configuration.

| Variable | Purpose |
| --- | --- |
| `HUBSPOT_PRIVATE_APP_ACCESS_TOKEN` | HubSpot Private App token (the scan API also accepts the token per request) |
| `HUBSPOT_API_BASE_URL` | HubSpot API base (default: `https://api.hubapi.com`) |
| `HUBSPOT_API_TIMEOUT` | Request timeout in seconds (default `30`) |
| `DB_HOST`, `DB_PORT`, `DB_NAME`, `DB_USER`, `DB_PASSWORD` | PostgreSQL connection settings |
| `DB_SCHEMA` | Application schema for `jobs` / `job_checkpoints` |
| `SECRET_KEY` | Flask secret key |
| `CONFIG_PASSWORD` | Encryption password used to encrypt stored scan credentials |
| `REQUEST_TIMEOUT`, `PAGINATION_DEFAULT_LIMIT` | Service tuning |

## `.env.example` setup

`.env.example` ships with placeholder values only (for example `HUBSPOT_PRIVATE_APP_ACCESS_TOKEN=replace-with-your-private-app-token`). Keep it that way — do not put real tokens or passwords into it.

## Local development

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate      # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

# Development server (default port 5000; HOST/PORT are configurable)
python app.py
```

Production-style WSGI serving:

```bash
gunicorn --bind 0.0.0.0:8000 wsgi:app
```

Syntax check:

```bash
python -m py_compile app.py wsgi.py config.py encrypter.py loki_logger.py utils.py api/*.py models/*.py services/*.py
```

## Docker setup

From the service directory:

```bash
docker compose build hubspot_deals_service_dev
docker compose up -d
docker compose ps
```

The development API is published on port `5200`; PostgreSQL on `5432`; Redis on `6379`. Optional development tools are available with the `dev` profile (pgAdmin on `:8080`, Redis Commander on `:8081`):

```bash
docker compose --profile dev up -d
```

Verify the stack:

```bash
curl --fail http://localhost:5200/api/health
curl --fail http://localhost:5200/api/pipeline/info
```

## API endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | API and database health summary |
| GET | `/health` | Lightweight application/database health check |
| GET | `/api/pipeline/info` | DLT pipeline name, destination, source, database health |
| GET | `/api/stats` | Service-wide job statistics |
| POST | `/api/scan/start` | Validate and start an asynchronous Deals scan |
| GET | `/api/scan/{scan_id}/status` | Scan status and checkpoint information |
| POST | `/api/scan/{scan_id}/pause` | Pause a running scan |
| POST | `/api/scan/{scan_id}/cancel` | Cancel a non-terminal scan |
| DELETE | `/api/scan/{scan_id}/remove` | Remove a terminal scan and its data |
| GET | `/api/scan/list` | List scans (`organizationId`, `limit`, `offset`) |
| GET | `/api/scan/statistics` | Scan statistics (`organizationId`) |
| GET | `/api/results/{scan_id}/tables` | Tables and row counts for a completed scan |
| GET | `/api/results/{scan_id}/result` | Paginated completed-scan data (`tableName`, `limit`, `offset`) |
| POST | `/api/maintenance/cleanup` | Remove old scan records (`daysOld`) |
| POST | `/api/maintenance/detect-crashed` | Mark stale running jobs (`timeoutMinutes`) |

`limit`/`offset` are validated (list max 100 rows, results max 500). Results default to `tableName=hubspot_deals`.

## Swagger / API documentation

Interactive Swagger UI: `GET /docs`  
OpenAPI JSON document: `GET /api/swagger.json`

Additional in-repo documentation:

- `docs/api-integration.md` — HubSpot CRM v3 integration details
- `docs/database-schema.md` — PostgreSQL and DLT schema design
- `docs/api-documentation.md` — service API reference
## How to start a scan / pipeline

`POST /api/scan/start` returns HTTP 202 after validation and begins background processing. Use a unique scan ID. Use a placeholder token in examples — never put a real token in files or terminal history you will share.

```bash
curl --request POST http://localhost:5200/api/scan/start \
  --header "Content-Type: application/json" \
  --data '{
    "config": {
      "scanId": "deals-example-001",
      "organizationId": "org-12345",
      "type": ["deals"],
      "auth": {"accessToken": "<HUBSPOT_PRIVATE_APP_TOKEN>"},
      "filters": {
        "properties": ["dealname", "amount", "dealstage", "closedate"],
        "includeArchived": false
      }
    }
  }'
```

Do not start a real scan just to test availability — use the health and pipeline endpoints instead.

## How to read pipeline / results

```bash
# Scan status (includes latest checkpoint)
curl http://localhost:5200/api/scan/deals-example-001/status

# List scans for an organization
curl "http://localhost:5200/api/scan/list?organizationId=org-12345&limit=20&offset=0"

# Tables (and row counts) produced by a completed scan
curl http://localhost:5200/api/results/deals-example-001/tables

# Paginated rows from a table (defaults to hubspot_deals)
curl "http://localhost:5200/api/results/deals-example-001/result?tableName=hubspot_deals&limit=50&offset=0"
```

## PostgreSQL and checkpoint / incremental-sync behavior

Two kinds of data are stored in PostgreSQL:

- **Application metadata** (SQLAlchemy): `jobs` and `job_checkpoints` in the configured application schema. Scan configuration is stored encrypted; `job_checkpoints` holds the phase, record count, page number, and next `after` cursor.
- **Extracted deals** (DLT): written to an organization-scoped schema named `hubspot_deals_<organization_id>` (hyphens become underscores), with a `hubspot_deals` table plus DLT bookkeeping tables (`_dlt_loads`, `_dlt_pipeline_state`, `_dlt_version`).

Checkpoints are saved every N pages and on pause, cancellation, error, and completion. On resume, the extraction service reads the latest cursor checkpoint and restarts the DLT source from that `after` cursor.

Rows carry scan, tenant/organization, page, source, and extraction-time metadata. The verified development example is database `hubspot_deals_data_dev`, schema `hubspot_deals_org_12345`, table `hubspot_deals` with 5 deals — these are observed examples, not hardcoded application constants.

## Testing and test-results

Sanitized verification evidence is committed under `test-results/`:

- `api-verification.md` — live API checks (health, pipeline, Swagger, scan status)
- `database-verification.md` — PostgreSQL schema and row-count checks
- `extraction-results.md` — recorded extraction scan `deals-final-002` with all 5 HubSpot deal IDs

Use `python -m py_compile ...` (see Local development) for a syntax check. This assessment repository does not ship an automated unit-test suite; the verification evidence lives in `test-results/`.

## Security considerations

- The HubSpot token is supplied per scan and stored encrypted (Fernet, keyed by `CONFIG_PASSWORD`). It is not written to logs and is never committed.
- `.env` is git-ignored; `.env.example` contains placeholders only.
- No real credentials, tokens, API keys, or database passwords are committed to this repository.
- The service's public HTTP routes are not independently authenticated. Deploy behind TLS and an authentication gateway or network restrictions before exposing it outside trusted development infrastructure.

## Development credentials warning

The Docker Compose configuration uses **development/demo-only** credentials for local services (`password123`, `redis123`, `admin123`). These are for local development only and **must be replaced** for any real deployment. Real HubSpot tokens, database passwords, and encryption passwords should come from your local `.env` and secrets management.

## DLT Generator attribution

This project was **structured/generated using the DLT Generator** (a template-driven tool for producing DLT-based extraction services), then customized into the completed HubSpot Deals integration documented here. The DLT Generator provided the initial project skeleton and multi-environment Docker scaffolding; the HubSpot CRM v3 client (deal pagination, property handling, rate-limit backoff), the DLT source, checkpoint orchestration, and the API are implemented specifically for this service.

## Author

Built by [Alemu Chamada](https://github.com/Alemu-chamada). Source repository: <https://github.com/Alemu-chamada/HubSpot-Deals-ETL-service>.
