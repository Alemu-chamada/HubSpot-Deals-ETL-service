# HubSpot Deals API integration

The extraction service reads HubSpot Deal records through the CRM v3 list endpoint:

```text
GET https://api.hubapi.com/crm/v3/objects/deals
```

The base URL comes from `HUBSPOT_API_BASE_URL` (default: `https://api.hubapi.com`); the service appends `/crm/v3/objects/deals`. No credentials are embedded in code or documentation.

## Authentication

Each scan supplies a HubSpot Private App token in `config.auth.accessToken`. The dedicated `HubSpotAPIService` sends it as `Authorization: Bearer <HUBSPOT_PRIVATE_APP_TOKEN>`. The token is encrypted when stored with the scan job and is never logged by the source or API service. The Private App needs the `crm.objects.deals.read` scope.

## Query behavior

`HubSpotAPIService.get_deals` constrains `limit` to 1–100 and forwards the following supported inputs:

| Scan filter | HubSpot query parameter | Behavior |
| --- | --- | --- |
| `properties` | `properties` | A list is serialized as a comma-separated property list. |
| `includeArchived` | `archived` | The service maps the public scan option to HubSpot CRM v3's parameter name. |
| pagination cursor | `after` | The cursor returned in `paging.next.after` is sent on the next request. |

For example, the request is equivalent to:

```text
GET /crm/v3/objects/deals?limit=50&properties=dealname,amount,dealstage&archived=false
```

## Pagination, validation, and loading

The DLT source reads `paging.next.after` until it is absent. Records without an `id`, a dictionary `properties` value, or a valid response envelope are rejected or fail the page with structured logging. Requested deal properties are normalized before DLT loads the `hubspot_deals` resource into PostgreSQL.

The source records the scan ID, organization ID, tenant ID, extraction timestamp, and page number with each yielded deal. DLT receives a `write_disposition="replace"` resource and `id` as its primary key.

## Resilience

Requests use `HUBSPOT_API_TIMEOUT` (falling back to `REQUEST_TIMEOUT`, default 30 seconds). The service:

- retries transient `requests` exceptions up to three times;
- detects HTTP 429, honors `Retry-After` when present (or `X-RateLimit-Reset`), and retries;
- raises `HubSpotAPIError` for other HTTP errors with the HubSpot message and status code;
- logs request metadata, duration, and retry events without logging the bearer token.

## Checkpoint and resume

The source saves checkpoints every ten pages and on pause, cancellation, error, and completion. A checkpoint contains the next `after` cursor, page number, record count, and phase. When a crashed job is resumed, the extraction service reads its latest cursor checkpoint and starts the DLT source from that cursor.

## Manual connectivity example

Use a local secret only; never paste a real token into source control.

```bash
curl --fail "https://api.hubapi.com/crm/v3/objects/deals?limit=1" \
  -H "Authorization: Bearer <HUBSPOT_PRIVATE_APP_TOKEN>"
```
