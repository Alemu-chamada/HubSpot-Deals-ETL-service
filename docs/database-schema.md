# PostgreSQL and DLT schema design

The service uses PostgreSQL for two distinct kinds of data:

1. application job metadata, managed by SQLAlchemy; and
2. extracted Deal data, managed by DLT.

The development Compose configuration uses database `hubspot_deals_data_dev`. Database and schema names are configuration values; they are not fixed in application logic.

## Application tables

SQLAlchemy creates these tables in the configured application schema (the development default is `hubspot_deals_dev`):

| Table | Purpose | Important fields |
| --- | --- | --- |
| `jobs` | Scan lifecycle and encrypted configuration | `id`/scan ID, `organizationId`, `type`, `status`, start/end/heartbeat times, `recordsExtracted`, encrypted `config`, `job_metadata` |
| `job_checkpoints` | Resume state for a scan | `job_id`, `phase`, `recordsProcessed`, `cursor`, `pageNumber`, `batchSize`, `checkpoint_data` |

`job_checkpoints.job_id` references `jobs.id`. Authentication is encrypted before it is stored in `jobs.config`; it must not be queried, copied into reports, or committed.

## DLT datasets

For a scan, `build_dataset_name` derives a PostgreSQL schema from the organization ID:

```text
hubspot_deals_<organizationId with hyphens replaced by underscores>
```

For example, the verified development environment contains `hubspot_deals_org_12345`. DLT creates the `hubspot_deals` table in that dataset schema along with its own `_dlt_*` bookkeeping tables. This dynamic schema design separates organizations at the schema level; result APIs derive the dataset from the completed scan metadata.

## Deal table

The DLT resource is named `hubspot_deals` and has primary key `id`. Its columns are inferred by DLT from records emitted by `services/data_source.py`; therefore this is a logical schema rather than a hand-maintained SQL `CREATE TABLE` contract. DLT flattens the nested `properties` object into columns named `properties__<property>`.

Expected business fields include `id`, `dealname`, `amount`, `dealstage`, `pipeline`, `closedate`, `createdate`, `hs_lastmodifieddate`, `hubspot_owner_id`, `description`, `dealtype`, and `is_closed` when HubSpot returns/requested properties. Values are normalized where possible: amount becomes a decimal, known date fields become datetimes, and boolean-like strings become booleans. The verified dataset contained `dealname`, `amount`, `pipeline`, `dealstage`, `closedate`, `id`, the extraction metadata below, flattened `properties__*` columns, and DLT's `_dlt_load_id` and `_dlt_id` columns.

Each record also includes `properties` plus extraction metadata:

| Field | Meaning |
| --- | --- |
| `scan_id` / `_scan_id` | Scan that produced the row |
| `tenant_id` | `tenant_id` or `tenantId` scan filter, otherwise organization ID |
| `_organization_id` | Organization from the scan configuration |
| `extracted_at` / `_extracted_at` | UTC extraction time |
| `_page_number` | HubSpot page that produced the row |
| `_source_service` | `hubspot_deals` |

Use quoted identifiers for the dynamically generated schema and inspect actual columns after a DLT run:

```sql
SELECT column_name, data_type
FROM information_schema.columns
WHERE table_schema = 'hubspot_deals_org_12345'
  AND table_name = 'hubspot_deals'
ORDER BY ordinal_position;
```

Do not hardcode the example schema in application code. For result retrieval, use `GET /api/results/{scan_id}/tables` to discover the dataset table and `GET /api/results/{scan_id}/result?tableName=hubspot_deals` to read it.
