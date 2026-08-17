# Service API documentation

The Flask-RESTX API is mounted under `/api`; Swagger UI is available at `http://localhost:5200/docs` and its OpenAPI JSON document is at `http://localhost:5200/api/swagger.json`. This service does not implement `/api/v1` routes or a client-facing JWT/API-key layer.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/api/health` | API and database health summary |
| GET | `/health` | Lightweight application/database health check |
| GET | `/api/stats` | Service job statistics |
| POST | `/api/scan/start` | Validate and start an asynchronous Deal scan |
| GET | `/api/scan/{scan_id}/status` | Scan status and checkpoint information |
| POST | `/api/scan/{scan_id}/pause` | Pause a pending/running scan |
| POST | `/api/scan/{scan_id}/cancel` | Cancel a non-terminal scan |
| DELETE | `/api/scan/{scan_id}/remove` | Remove a terminal scan and its data |
| GET | `/api/scan/list` | List scans; optional `organizationId`, `limit`, `offset` |
| GET | `/api/scan/statistics` | Scan statistics; optional `organizationId` |
| GET | `/api/results/{scan_id}/tables` | Tables and counts for a completed scan |
| GET | `/api/results/{scan_id}/result` | Paginated completed-scan data; `tableName`, `limit`, `offset` |
| GET | `/api/pipeline/info` | DLT pipeline and PostgreSQL connectivity information |
| POST | `/api/maintenance/cleanup` | Remove old scan records; JSON `daysOld` |
| POST | `/api/maintenance/detect-crashed` | Mark stale running jobs; query `timeoutMinutes` |

`limit` and `offset` are validated. The list endpoint has a maximum of 100 rows; the results endpoint has a maximum of 500 rows. Results default to `tableName=hubspot_deals`.

## Start a scan

`POST /api/scan/start` returns HTTP 202 after request validation and begins background processing. Use a unique scan ID. The bearer token below is a placeholder and is encrypted when persisted; do not place a real token in files, terminals with shared history, or documentation.

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

Check progress and completed data:

```bash
curl http://localhost:5200/api/scan/deals-example-001/status
curl http://localhost:5200/api/results/deals-example-001/tables
curl "http://localhost:5200/api/results/deals-example-001/result?tableName=hubspot_deals&limit=50&offset=0"
```

## Behavior and errors

Request validation errors return HTTP 400. Duplicate scan IDs return 409. Missing scans return 404 where applicable. Result and table endpoints require a completed scan and return 400 otherwise. Unexpected errors return 500 with a response summary; server logs contain request context but must not contain credentials.

The scan request itself is authenticated to HubSpot with the supplied Private App token. The service's public HTTP routes are not independently authenticated by this repository; deploy it behind appropriate network controls, TLS, and an authentication gateway when exposed beyond trusted development infrastructure.
