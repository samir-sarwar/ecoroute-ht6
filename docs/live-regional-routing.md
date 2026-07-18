# Live regional carbon routing

## What the production path does

EcoRoute evaluates every eligible physical endpoint using its configured energy coefficients and
a timestamped carbon-intensity reading for its grid. With Electricity Maps, a reading can be
looked up directly by zone or by a supported data-center provider and region. The router combines
carbon, cost, latency, quality, and evidence weights; the `eco` preset gives carbon the largest
weight but does not promise that every selected route is the absolute lowest-carbon candidate.

The defensible claim is:

> EcoRoute routed the request among the configured eligible endpoints using current regional grid
> carbon intensity and the recorded policy weights.

It is not defensible to claim that EcoRoute discovered or selected OpenAI's cleanest physical data
center. A normal OpenAI inference response does not disclose that data center or its electricity
metering.

## Configure live grid data

Set demo mode off and provide Electricity Maps credentials:

```dotenv
ECOROUTE_DEMO_MODE=false
ECOROUTE_CARBON_PROVIDER=auto
ELECTRICITY_MAPS_API_KEY=replace-with-server-side-token
ELECTRICITY_MAPS_BASE_URL=https://api.electricitymaps.com/v4
ECOROUTE_CARBON_CACHE_SECONDS=300
ECOROUTE_CARBON_FRESHNESS_TARGET_MINUTES=15
```

`auto` selects Electricity Maps only when the key is present. Without a key it reports the carbon
provider as unconfigured. To use an independently deployed Carbon Aware service instead, set
`ECOROUTE_CARBON_PROVIDER=carbon_aware` and `CARBON_AWARE_BASE_URL`; Carbon Aware supports direct
zone lookup but not EcoRoute's Electricity Maps data-center lookup mode.

## Register regional OpenAI endpoints

Use separate eligible OpenAI projects/keys for every regional endpoint. Add each credential
variable to `ECOROUTE_ALLOWED_CREDENTIAL_ENVS`. Example US endpoint fields:

```json
{
  "provider": "openai",
  "baseUrl": "https://us.api.openai.com/v1",
  "credentialRef": "env:OPENAI_US_API_KEY",
  "region": "us",
  "gridZone": "EXPECTED_ELECTRICITY_MAPS_ZONE",
  "processingLocationEvidence": "provider_contract",
  "gridLookupMode": "zone",
  "gridAttribution": "regional_proxy",
  "energyEvidence": "estimated"
}
```

Use `https://eu.api.openai.com/v1`, an eligible EU project, and a separate server-side key for the
EU endpoint. The gateway rejects `processingLocationEvidence=provider_contract` on the generic
`api.openai.com` hostname or when the hostname and region disagree.

`regional_proxy` means the configured grid zone is a transparent proxy for the provider region;
it is not asserted to be the physical serving data center. If Electricity Maps supports a precise
provider/region mapping for an endpoint, use:

```json
{
  "gridLookupMode": "data_center",
  "gridDataCenterProvider": "provider-key",
  "gridDataCenterRegion": "provider-region",
  "gridZone": "EXPECTED_RETURNED_ZONE",
  "gridAttribution": "electricity_maps_data_center"
}
```

EcoRoute sends the provider/region lookup to Electricity Maps and rejects the response unless its
returned zone matches `gridZone`. If Electricity Maps does not map the provider region, carbon
accounting remains unavailable rather than falling back to the caller's IP or a fabricated value.

## Evidence and reporting

The endpoint test checks both provider health and the grid mapping. Every completed request stores
the selected and baseline grid source, observed timestamp, estimation metadata, processing-
location evidence, grid attribution, coefficient version, and claim scope. Responses include
`X-EcoRoute-Carbon-Accounting` and `X-EcoRoute-Grid-Attribution` headers. Requests with missing
location or grid evidence retain energy/cost accounting but are excluded from carbon totals,
avoided-carbon metrics, charts, and Impact Framework observations.
