# API verification evidence

The existing Docker Compose development stack was checked without starting a new scan.

| Check | Result |
| --- | --- |
| `GET /api/health` | HTTP 200 |
| `GET /api/pipeline/info` | HTTP 200 |
| Swagger UI at `/docs` | HTTP 200 |
| OpenAPI document at `/api/swagger.json` | HTTP 200 |
| `GET /api/scan/deals-final-002/status` | HTTP 200 |
| `GET /api/results/deals-final-002/tables` | HTTP 200 |

`docker compose ps` reported the development PostgreSQL, Redis, and application service as healthy during verification.
