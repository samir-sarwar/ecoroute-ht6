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

For Azure OpenAI `Standard` and regional `Provisioned Managed` deployments, the provider contract
states that inference data is processed in the deployment region. EcoRoute can therefore choose
among operator-configured Azure regional deployments using their current grid signals. It is still
not defensible to claim provider facility-level electricity metering: the carbon result is an
operational estimate from endpoint coefficients and mapped regional grid intensity.

The business application continues to use one OpenAI-compatible EcoRoute base URL. EcoRoute
replaces the logical model alias with the selected Azure deployment name and sends the request to
that deployment's resource URL and credential; no per-request client change is needed.

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

## Register Azure OpenAI endpoints

Global Standard deployments can be registered for real completions, cost accounting, and
coefficient-based energy estimates. Because Azure may process a Global request in any supported
region, register it with `region=global`, `gridZone=unknown`, and `gridAttribution=unknown`. It is
then excluded from carbon totals and cannot be used to prove a clean/dirty regional route.

Create the same model deployment in at least two Azure OpenAI resources/regions. The deployment
must use `Standard` or the regional form of `Provisioned Managed` to participate in regional
carbon routing. Data Zone deployments are currently rejected because they do not prove one exact
processing region. Azure
documents the [regional data-processing behavior](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/models-sold-directly-by-azure-region-availability)
and its [OpenAI v1 endpoint format](https://learn.microsoft.com/en-us/azure/foundry/foundry-models/concepts/endpoints).

Put each resource key only in the gateway environment and include its variable in
`ECOROUTE_ALLOWED_CREDENTIAL_ENVS`. Register one physical endpoint per regional deployment:

```json
{
  "provider": "azure_openai",
  "baseUrl": "https://my-canada-resource.openai.azure.com/openai/v1",
  "credentialRef": "env:AZURE_OPENAI_CANADA_KEY",
  "physicalModel": "my-gpt-deployment",
  "azureDeploymentType": "standard",
  "region": "canada-central",
  "gridZone": "CA-ON",
  "processingLocationEvidence": "provider_contract",
  "gridLookupMode": "zone",
  "gridAttribution": "regional_proxy",
  "energyEvidence": "estimated"
}
```

`physicalModel` is the Azure deployment name, not merely the base model name. The endpoint test
authenticates to `GET /openai/v1/models` using Azure's `api-key` header and reports whether the
configured deployment appears in that model list. A successful live completion is the final
data-plane validation that the deployment exists.

The API also accepts the Azure `services.ai.azure.com` resource hostname. It rejects non-HTTPS
URLs, paths other than `/openai/v1`, missing credential references, and Azure deployment metadata
attached to another provider.

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
`X-EcoRoute-Carbon-Accounting`, `X-EcoRoute-Grid-Attribution`,
`X-EcoRoute-Processing-Region`, and `X-EcoRoute-Provider-Deployment` headers. Requests with missing
location or grid evidence retain energy/cost accounting but are excluded from carbon totals,
avoided-carbon metrics, charts, and Impact Framework observations.

## Demo behavior

The two frontier demo endpoints are shaped like Azure OpenAI resources in Canada Central and
Sweden Central, so routing, registry, response provenance, and grid-selection behavior exercise the
same path. In `ECOROUTE_DEMO_MODE=true`, provider calls and grid readings remain deterministic
fixtures, the evidence remains `simulated`, and no Azure key or network call is used. Set demo mode
off and register real endpoints to enable the live Azure adapter.
