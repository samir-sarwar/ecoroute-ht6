# Measurement methodology and evidence boundary

EcoRoute keeps four evidence levels distinct:

| Level | Meaning |
|---|---|
| `measured` | Hardware counters for the relevant window |
| `estimated` | Observed request data combined with configured coefficients |
| `stale` | Last valid measurement beyond its freshness target |
| `simulated` | Deterministic demonstration fixtures |

The default demo endpoint energy, grid intensity, node power, cost, latency, and savings values
are synthetic fixtures. They demonstrate control-plane behavior and unit calculations; they do
not constitute an environmental claim. Outside demo mode, EcoRoute fails closed when no live
carbon provider is configured; it does not silently use the bundled fixture.

Carbon readings are cached for five minutes and refreshed every two minutes. Electricity Maps v4
is selected automatically when `ELECTRICITY_MAPS_API_KEY` is present and requests flow-traced,
lifecycle carbon intensity at five-minute granularity. Carbon Aware must be selected explicitly in
non-demo deployments. A cached live reading retains its source, observation time, provider
estimation flag, estimation method, emission-factor type, temporal granularity, and lookup mode.
The freshness target is 15 minutes by default. A database fallback beyond that target is labeled
`stale`; if no acceptable reading exists, routing records grid state `unknown` and missing data is
never scored as zero emissions. The credential-free fixture remains `simulated`.

Endpoint location evidence is independent from grid-data evidence. `processingLocationEvidence`
records whether processing location follows a provider contract, an operator declaration, a
self-hosted location, an unknown location, or a simulation. `gridAttribution` records whether the
grid came from an Electricity Maps data-center mapping, a known physical grid, a regional proxy,
an operator declaration, an unknown mapping, or a simulation. Electricity Maps data-center
lookups must return the configured expected zone or the reading is rejected. Unknown processing
or grid attribution makes carbon accounting unavailable, applies the worst routing evidence
penalty, and excludes the request from carbon totals and avoided-carbon claims.

For OpenAI, `provider_contract` validation is limited to matching US and EU regional API
hostnames. The operator must still use an eligible project configured for regional processing.
EcoRoute cannot verify that project setting from a normal inference key, cannot see the physical
serving data center, and therefore never describes an OpenAI result as measured data-center
energy or as routing to OpenAI's cleanest data center. A regional-proxy configuration is reported
as a provider-region grid proxy; an Electricity Maps data-center mapping is reported separately.

Hosted endpoint operational energy is calculated from a versioned fixed request coefficient plus
input/output-token coefficients. Operational carbon is energy multiplied by the endpoint zone's
grid intensity. The configured baseline endpoint is evaluated with the same token counts. Raw
carbon deltas are retained; headline avoided carbon is floored at zero, while increases remain
visible in detail.

Cache attribution subtracts an explicitly estimated lookup-energy coefficient from avoided model
energy. Router overhead is included on non-cache optimized routes. Node optimization is compared
against a measured or clearly simulated benchmark and is not added again to route savings.

The Impact Framework YAML export contains precomputed observations and source/evidence labels.
It is an auditable record of assumptions, not third-party assurance. Because v1 does not contain a
robust embodied-carbon model, the UI says **Operational carbon intensity**, never “SCI score.”
The export is re-runnable with the pinned Green Software Foundation CLI using `make
impact-validate`; the empty pipeline intentionally retains EcoRoute's precomputed observations.

Exports use methodology version `ecoroute-v2` and preserve the endpoint coefficient version,
carbon source, attribution method, UTC hour, request count, duration, energy, carbon, and evidence
level. Exports omit carbon observations whose accounting-availability flag is false. Operational
inference energy is in scope; embodied carbon, networking outside measured node telemetry, and
end-user device energy are out of scope.
