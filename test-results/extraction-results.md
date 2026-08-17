# HubSpot Deals extraction evidence

## Recorded successful scan

- Scan ID: `deals-final-002`
- Source: HubSpot CRM v3 Deals endpoint
- Destination resource: `hubspot_deals`
- Outcome: completed successfully in the existing development environment

The assessment record identifies these five HubSpot Deal IDs as extracted by the successful scan:

```text
514354627819
514370736315
514543447235
514748613860
514748613872
```

No credential, bearer token, or customer property value is included in this evidence.

## Reproduction boundaries

This report records an existing successful scan. It does not initiate another HubSpot request. To inspect the stored result through the service API after configuring a local environment, use:

```bash
curl http://localhost:5200/api/scan/deals-final-002/status
curl http://localhost:5200/api/results/deals-final-002/tables
curl "http://localhost:5200/api/results/deals-final-002/result?tableName=hubspot_deals&limit=50&offset=0"
```
