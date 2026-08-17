# Database verification evidence

Read-only verification of the existing development PostgreSQL database found:

| Item | Observed value |
| --- | --- |
| Database | `hubspot_deals_data_dev` |
| Dataset schema | `hubspot_deals_org_12345` |
| Deal table | `hubspot_deals` |
| Deal rows | 5 |
| DLT tables | `_dlt_loads`, `_dlt_pipeline_state`, `_dlt_version` |

The observed Deal table contains normalized Deal columns (`dealname`, `amount`, `pipeline`, `dealstage`, `closedate`, and `id`), scan and organization metadata, flattened HubSpot property columns (`properties__*`), and DLT identifiers (`_dlt_load_id`, `_dlt_id`).

The verification query was read-only:

```sql
SELECT COUNT(*)
FROM hubspot_deals_org_12345.hubspot_deals;
```

The schema is derived from the scan organization ID. The names above document the verified development run and must not be hardcoded into application configuration.
