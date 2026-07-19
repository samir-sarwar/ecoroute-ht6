# Live hosted end-to-end demo

This is the operator flow for demonstrating as much real EcoRoute behavior as the current
integration permits. It uses real Azure OpenAI completions, the deployed FreeSOLO Northstar
support SLM, the optional deployed FreeSOLO prompt router, live Electricity Maps readings, the
real cache, real fallback handling, and the real audit/report pipeline.

The boundary is important: provider calls, returned token counts, latency, cache decisions, and
grid readings can be live. Hosted-model energy and carbon are not provider-metered. They are
operational estimates calculated from versioned endpoint coefficients and the live regional grid
reading. Cost is calculated from returned token counts and the prices entered for the endpoint.

## 1. Credentials and deployments

For the full flow, put these server-side values in the repository-root `.env`:

```dotenv
ECOROUTE_DEMO_MODE=false

FREESOLO_API_KEY=replace-with-freesolo-key
FREESOLO_ROUTER_BASE_URL=https://clado-ai--freesolo-lora-serving.modal.run/v1
FREESOLO_ROUTER_MODEL_ID=flash-1784392297-e2a42199

AZURE_OPENAI_CANADA_KEY=replace-with-ecouse-resource-key
# Only needed if you later add a second Azure resource:
AZURE_OPENAI_SWEDEN_KEY=replace-with-second-resource-key

ELECTRICITY_MAPS_API_KEY=replace-with-electricity-maps-token
ECOROUTE_CARBON_PROVIDER=auto
ELECTRICITY_MAPS_BASE_URL=https://api.electricitymaps.com/v4

# Live-grid counterfactual overlay for the temporary Global Standard demo.
ECOROUTE_DEMO_GLOBAL_REGION_OVERLAY=true
ECOROUTE_DEMO_GLOBAL_REGION_CANDIDATES=east-us-2=US-MIDA-PJM,south-central-us=US-TEX-ERCO,sweden-central=SE-SE3,poland-central=PL
ECOROUTE_DEMO_GLOBAL_REFERENCE_REGION=east-us-2
```

No `OPENAI_API_KEY` is needed when every general/frontier completion endpoint is Azure OpenAI.
The FreeSOLO key is separate because the support SLM and optional prompt router are served by
FreeSOLO, not Azure.

An Azure API key authenticates one Azure resource. Reuse it for multiple deployments in that same
resource. A second regional Azure resource has a different base URL and normally a different key;
that is why two keys are shown above. Canada and Sweden are only the seeded example regions. Add
any supported regional resources by adding their key variable names to
`ECOROUTE_ALLOWED_CREDENTIAL_ENVS` and registering their endpoints.

Create these real Azure deployments before configuring EcoRoute:

| EcoRoute role | Suggested tier | Azure deployment |
| --- | --- | --- |
| low/medium general route | `small` or `standard` | a lower-cost regional chat deployment |
| high-complexity baseline/fallback | `frontier` | a regional frontier deployment in resource A |
| high-complexity alternate region | `frontier` | the same model/deployment settings in resource B |

Global Standard deployments are accepted for real hosted completions, cost accounting, and
coefficient-based energy estimates. They are registered with `region=global`, `gridZone=unknown`,
and `gridAttribution=unknown`, so they are deliberately excluded from regional grid/carbon claims.
Use regional `Standard` or regional `Provisioned Managed` endpoints when the demonstration needs
real grid-aware carbon routing. Data Zone deployments are not currently accepted.

Restart the services after changing `.env`:

```bash
docker compose up --build -d --wait
curl --fail http://localhost:8000/readyz
```

Do not run `scripts/demo-smoke` for the live presentation: that script intentionally controls the
fixture-only demo path.

## 2. Register the real endpoints

Go to **Control Center → Model Endpoints** at <http://localhost:3000/model-endpoints>. Create new
live endpoints rather than changing the seeded `demo-*` rows, so a later seed run cannot overwrite
the live configuration.

### FreeSOLO Northstar support SLM

Click **Add endpoint** and use:

| Field | Value |
| --- | --- |
| Name | `live-northstar-support-slm` |
| Provider | `freesolo` |
| Physical model | `flash-1784393778-a0fbce92` |
| Base URL | `https://clado-ai--freesolo-lora-serving.modal.run/v1` |
| Credential reference | `env:FREESOLO_API_KEY` |
| Tier | `specialized` |
| Capabilities | `text`, `json_schema`, `streaming` |
| Self-hosted | off |
| SLM profile ID | copy the value from **Edit** on seeded `demo-support-slm` |
| Region / grid zone | `unknown` unless FreeSOLO supplies defensible location evidence |
| Processing/grid evidence | `unknown` unless documented evidence is available |
| Energy evidence | `estimated` |

Do not invent a FreeSOLO region to make the carbon number appear. The route and energy estimate
still work; carbon for an unattributed hosted SLM request is correctly shown as unavailable.

Click **Test**. The adapter now checks both the FreeSOLO service health route and this exact adapter
ID. A healthy result is a real network/authentication check.

The support model is Qwen/Qwen3.5-4B fine-tuned on the Northstar ecommerce support data. For a
quick live preflight run five held-out prompts; for the defensible evaluation run all 331:

```bash
python3.12 scripts/eval_deployment.py support 5
python3.12 scripts/eval_deployment.py support
```

The five-prompt run proves connectivity, not model quality. Only the full held-out run supports a
gate claim.

### Azure general and frontier endpoints

For the two Global Standard deployments in the `ecouse` resource, both endpoints reuse the same
base URL and credential reference:

| Field | GPT-5.4 mini | GPT-5.4 |
| --- | --- | --- |
| Name | `azure-gpt54-mini-global` | `azure-gpt54-global` |
| Provider | `azure_openai` | `azure_openai` |
| Physical model | `gpt-5.4-mini` | `gpt-5.4` |
| Azure deployment type | `Global Standard` | `Global Standard` |
| Base URL | `https://ecouse.openai.azure.com/openai/v1` | same |
| Credential reference | `env:AZURE_OPENAI_CANADA_KEY` | same |
| Tier | `small` | `frontier` |
| Input USD / 1M tokens | `0.75` | `2.50` |
| Output USD / 1M tokens | `4.50` | `15.00` |
| Region / grid zone | `global` / `unknown` | `global` / `unknown` |
| Processing-location evidence | `provider_contract` | `provider_contract` |
| Grid attribution | `unknown` | `unknown` |
| Energy evidence | `estimated` | `estimated` |

Select `text`, `json_schema`, `tools`, and `streaming` capabilities. The prices above are the Azure
public retail Global Standard meters checked on 2026-07-18; verify them against the subscription's
actual billing agreement before making financial claims. The Azure portal's displayed
`/openai/responses?api-version=...` URL is an operation-specific endpoint, not the EcoRoute Base
URL. EcoRoute uses the v1 Chat Completions path shown in the table.

These Global endpoints are sufficient to show real low/medium-to-mini and high-to-full hosted
calls. They are not sufficient to prove the clean/dirty regional branch because Azure can process
a Global Standard request in any supported Azure region and does not return a per-request
processing region.

For the temporary demo, `ECOROUTE_DEMO_GLOBAL_REGION_OVERLAY=true` scans the configured candidate
zones using live Electricity Maps readings. The cleanest reading is shown as a **Demo green
target**, and the impact panel calculates the counterfactual result of running the selected model's
configured energy estimate there versus the configured reference region. The live grid readings,
provider call, tokens, and arithmetic are real. The target location is not an actual Azure routing
claim: Global Standard still chooses the processing region, and EcoRoute cannot force or verify
that choice. Counterfactual values remain separate from attributed carbon totals and exports.
If an endpoint's energy coefficients are still zero, the overlay uses visibly labeled temporary
tier-based demo assumptions (`temporary_demo_tier_assumption_v1`) so the comparison is non-zero.
Those assumptions are simulated and must be replaced with cited endpoint coefficients before any
environmental claim is made.

For regional carbon routing, create one physical endpoint per Azure regional deployment. For each
endpoint:

- Provider: `azure_openai`
- Base URL: `https://YOUR-RESOURCE.openai.azure.com/openai/v1`
- Credential reference: the matching `env:AZURE_OPENAI_..._KEY`
- Physical model: the Azure deployment name sent in the provider `model` field
- Azure deployment type: `Standard (regional)` or regional `Provisioned Managed`
- Region and grid zone: the real Azure region and its Electricity Maps zone
- Processing-location evidence: `provider_contract`
- Grid attribution: `regional_proxy`, unless using a verified Electricity Maps data-center mapping
- Energy evidence: `estimated`

Enter the actual input/output price for that deployment. Enter energy coefficients only from the
methodology you are prepared to cite, and give them a meaningful coefficient version. The UI does
not pretend Azure exposes per-request kWh.

Click **Test** on every endpoint. Require provider status `healthy`; for endpoints included in a
carbon claim, also require a timestamped grid reading and `carbon accounting available`.

## 3. Wire `support-default` only to live endpoints

Still in **Model Endpoints**, find **Logical model mappings → support-default → Edit mapping**:

1. Select only the new real general, support-SLM, and frontier endpoints in **Endpoint pool**.
2. Set **Baseline** to the regional frontier endpoint representing the normal no-EcoRoute route.
3. Set **Required fallback** to a healthy real frontier endpoint.
4. Leave the alias `support-default` unchanged and click **Save mapping**.

The Northstar app always sends `model: support-default`. EcoRoute replaces that alias with the
selected endpoint's physical model/deployment parameter. The Overview and Request Audit screens
now show both values explicitly.

## 4. Create the presentation policy

Go to **Routing Policies**, edit the active family, and include only the same live endpoints in the
**Endpoint allowlist**. Recommended presentation settings:

```text
carbon 0.75
cost 0.10
latency 0.05
quality 0.05
evidence 0.05
max cost increase 20%
semantic cache on
quality fallback on
experimental models off
```

The weights must sum to 1.00. Click **Save new version**, then **Activate for support-default**.
Policy edits are immutable versions; saving without activating does not change live traffic.

The explicit dirty-grid specialized rule evaluates the live grid at the configured baseline
endpoint. The high-complexity path does not use a separate hardcoded dirty branch: it requires a
frontier tier and then applies the configured weighted score among eligible frontier endpoints.
With equivalent frontier deployments in two regions and carbon weighted at 0.75, the lower-carbon
eligible region should win. Confirm the candidate scores in the live trace instead of promising a
particular region in advance.

### Show both clean and dirty branches without fake grid data

This section requires a regional baseline endpoint; a Global Standard baseline correctly reports
its grid as unknown and cannot enter a clean or dirty regional state. Read the regional baseline
intensity `I` from **Settings → Carbon zones**. Change only the policy thresholds;
the grid reading remains real:

- Clean policy version: set `Clean threshold` above `I`, with `Dirty threshold` still higher.
- Dirty policy version: set `Dirty threshold` below `I`, with `Clean threshold` still lower.

For example, if the real reading is 438 gCO₂e/kWh, clean can be `450 / 550` and dirty can be
`300 / 400`. Save and activate each version as needed. This demonstrates two real policy decisions
against the same real reading; say explicitly that you changed the business threshold, not the
electricity data.

Increment **Cache namespace version** when moving between the clean and dirty demonstrations, or
use a new prompt. Otherwise a legitimate cache hit can bypass the provider route you want to show.

## 5. Presentation flow and exact prompts

Put <http://localhost:3001> and the Control Center **Overview** at <http://localhost:3000> side by
side. The Overview refreshes and displays:

- `model: support-default` from the client;
- complexity, task, risk, routing-grid state, and route reason;
- the selected endpoint and exact provider `model` parameter;
- live-provider, exact/semantic-cache, or simulated-provider execution status;
- region, tier, tokens, latency, candidate scores/exclusions, and baseline marker;
- per-request and cumulative baseline-versus-EcoRoute energy, carbon, and cost estimates.

Run the following sequence.

1. **Clean, low complexity → general Azure endpoint.** Activate the clean policy version. Ask:

   > What is your return window for unused items?

   Expect `policy_qa`, low complexity, and the specialized endpoint excluded as
   `specialized_reserved_for_dirty_grid`. The provider parameter should be the real Azure general
   deployment.

2. **Dirty, low complexity → real support SLM.** Activate the dirty version and increment the cache
   namespace. Ask:

   > Can I return a final-sale item?

   Expect `dirty_grid_specialized_preference`, provider `freesolo`, and model
   `flash-1784393778-a0fbce92`.

3. **Dirty, medium complexity → real support SLM.** Ask:

   > Summarize the return and refund timing policies in three short bullets.

   Expect `summarization`, medium complexity, SLM eligible, and the real specialized route.

4. **High risk/complexity → lower-carbon eligible frontier region.** Ask:

   > Compare this return policy with Ontario consumer law and assess lawsuit risk.

   The deterministic safety boundary marks this legal request high risk before any learned router
   can downgrade it. Non-frontier candidates show `frontier_required`; the real frontier candidates
   are weighted using their live grid readings. The trace shows which region won and why.

5. **Exact cache → no provider call.** Return to the clean policy version and increment the cache
   namespace once. Ask this twice, unchanged:

   > What is your return window for unused items?

   The first request is a miss; the second should say **CACHE REUSE**, `exact`, and **No upstream
   call**.

6. **Semantic cache → no provider call.** Immediately paraphrase the same public-policy question:

   > How many days do I have to send back something unused?

   Expect `semantic` only if the real embedding similarity clears the configured 0.94 threshold and
   the context hash is unchanged. If it misses, that is a valid conservative decision, not a demo
   failure.

7. **Real transport fallback, optional.** Register a deliberately invalid Azure deployment name as
   the preferred eligible endpoint and keep a valid frontier endpoint as Required fallback. Send a
   new legal prompt. The first provider attempt should fail for real, and Request Audit should show
   the second real attempt plus `fallback=true`. Remove or disable the fault-injection endpoint
   afterward. Do not use **Force next quality failure** for this claim; that button exists only in
   fixture demo mode.

## 6. Where to prove each part

- **Overview:** live route chain, physical model parameter, candidates, baseline-vs-actual graph,
  and Carbon/Energy/Cost toggles.
- **Request Audit:** open the newest request for normalized features, classification, exact
  exclusions, scores, provider attempts, returned token counts, and impact evidence.
- **Settings:** verify the live Electricity Maps source, zone, observation timestamp, and freshness.
- **Impact Reports:** filter to the last hour and export request CSV or Impact Framework YAML.
- **Model Endpoints:** rerun provider/grid health checks before presenting.

Do not use the Routing Policies dry-run simulator as evidence of a provider call. It is useful for
checking candidate eligibility but intentionally performs no inference.

## 7. Claims to make—and not make

Defensible:

- EcoRoute made real provider calls to the physical models shown in the audit.
- The deployed Northstar support SLM handled the specialized prompts shown.
- The router used timestamped Electricity Maps data for endpoints with valid location attribution.
- Token usage, latency, cache reuse, and transport attempts came from the live request path.
- Cost/energy/carbon are versioned operational estimates, with evidence visible in the report.

Not defensible:

- Azure or FreeSOLO supplied facility-level per-request energy measurements.
- A carbon number is real when the endpoint location/grid attribution is unknown.
- A five-example FreeSOLO smoke test proves model quality.
- High complexity has a hardcoded “dirty means Sweden” route. It requires frontier quality and uses
  the configured score among currently eligible frontier endpoints.
