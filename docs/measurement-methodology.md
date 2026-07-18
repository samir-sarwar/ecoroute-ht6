# Measurement methodology and evidence boundary

EcoRoute keeps four evidence levels distinct:

| Level | Meaning |
|---|---|
| `measured` | Hardware counters for the relevant window |
| `estimated` | Observed request data combined with configured coefficients |
| `stale` | Last valid measurement beyond its freshness target |
| `simulated` | Deterministic demonstration fixtures |

The default endpoint energy, grid intensity, node power, cost, latency, and savings values are
synthetic fixtures. They demonstrate control-plane behavior and unit calculations; they do not
constitute an environmental claim.

Carbon readings are cached for five minutes and refreshed every two minutes. A cached live
reading retains its source and observation time. A database fallback is labeled `stale`; if no
reading exists, routing records grid state `unknown` and missing data is never scored as zero
emissions. The credential-free fixture remains `simulated`.

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

Exports use methodology version `ecoroute-v1` and preserve the endpoint coefficient version,
carbon source, attribution method, UTC hour, request count, duration, energy, carbon, and evidence
level. Operational inference energy is in scope; embodied carbon, networking outside measured node
telemetry, and end-user device energy are out of scope.
