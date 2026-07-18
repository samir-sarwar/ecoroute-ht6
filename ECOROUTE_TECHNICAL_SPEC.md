# EcoRoute AI Gateway

## Decision-Complete Technical Specification

**Document status:** Implementation specification  
**Product:** EcoRoute AI Gateway  
**Primary demo:** E-commerce customer-support AI  
**Primary deployment:** Laptop Docker Compose, with an optional NVIDIA Linux host  
**Last reviewed:** 2026-07-15

---

## 0. How to use this document

This document is the source of truth for building EcoRoute from an empty repository. An implementation agent must follow the interfaces, defaults, state transitions, routing order, and acceptance criteria below. When a dependency has changed since this document was written, use the linked official documentation, choose the latest stable compatible release, and record the resolved version in the lockfile. Do not silently change product behavior to match a dependency's convenience.

The words **MUST**, **MUST NOT**, **SHOULD**, and **MAY** are normative:

- **MUST / MUST NOT:** required for the project to be complete.
- **SHOULD:** expected unless a concrete incompatibility is documented.
- **MAY:** optional enhancement that cannot block the core demo.

The implementation must distinguish four evidence levels everywhere impact is displayed or exported:

| Evidence level | Meaning | UI label |
|---|---|---|
| `measured` | Read from hardware counters for the relevant time window | Measured |
| `estimated` | Calculated from configured coefficients and observed request data | Estimated |
| `stale` | Uses the last valid measurement after its freshness window | Estimated (stale) |
| `simulated` | Comes from deterministic demo fixtures | Simulated |

Never merge these categories in a chart without a visible per-series label. Never describe estimated or simulated data as verified, exact, or audit-ready.

### 0.1 Required deliverables

The finished repository must contain:

1. A working OpenAI Chat Completions-compatible gateway.
2. A business-facing control-center web application that configures and demonstrates every core capability.
3. A separate customer-facing e-commerce support demo that sends ordinary chat requests through the gateway without exposing EcoRoute internals.
4. Persistent model, policy, request, cache, training, telemetry, and impact records.
5. A semantic cache with conservative safety rules.
6. A FreeSolo-trained prompt complexity router.
7. A FreeSolo-trained e-commerce support SLM.
8. Gemini-backed synthetic dataset generation and review.
9. Carbon-aware model and endpoint routing.
10. Quality validation and frontier-model fallback.
11. Cost policies that prevent unapproved cost increases.
12. A self-hosted node agent with real telemetry where supported and deterministic simulation elsewhere.
13. Live dashboard updates, Prometheus metrics, and Impact Framework exports.
14. Docker Compose setup, seed data, tests, and a repeatable demo runbook.

### 0.2 Definition of done

EcoRoute is done when a reviewer can:

- Start the laptop stack with one documented command.
- Point an OpenAI SDK client at EcoRoute by changing only its base URL and API key.
- Send a customer message in the e-commerce support demo and receive a normal support response.
- See that same request's route, model, cost, carbon evidence, latency, and policy reason appear live in the separate EcoRoute control center.
- Demonstrate exact and semantic cache hits without invoking an upstream model.
- Demonstrate a dirty-grid low-complexity prompt routed to the specialized SLM.
- Demonstrate a complex or risky prompt routed to the configured frontier fallback.
- Force a small-model quality failure and observe one automatic fallback.
- Compare baseline and optimized self-hosted runs using measured or clearly simulated telemetry.
- Generate and validate both FreeSolo training configurations.
- Export an Impact Framework manifest that preserves evidence sources and assumptions.
- Run all required unit, integration, contract, and end-to-end tests successfully.

---

## 1. Product definition

### 1.1 One-sentence description

EcoRoute is an OpenAI-compatible gateway and control center that reduces the operational carbon and compute cost of LLM traffic by safely reusing responses, classifying request complexity, selecting an eligible model or region, and optimizing customer-controlled inference hosts.

### 1.2 Customer adoption model

A customer changes:

```python
client = OpenAI(
    api_key=os.environ["OPENAI_API_KEY"],
    base_url="https://api.openai.com/v1",
)
```

to:

```python
client = OpenAI(
    api_key=os.environ["ECOROUTE_GATEWAY_KEY"],
    base_url="http://localhost:8000/v1",
)
```

The request's `model` becomes a **logical model alias**, such as `support-default`. EcoRoute maps that alias to a customer-approved endpoint pool. The gateway cannot use a smaller model, a different provider, or a different region unless that endpoint is explicitly registered and allowed by policy.

EcoRoute promises the same API shape and task intent, not byte-identical output across models. Small-model responses are protected by domain, confidence, capability, and output-format checks. A request falls back to the configured frontier endpoint when those checks fail.

### 1.3 Primary demo business workflow

The built-in demonstration represents an online retailer named **Northstar Outfitters**. The specialized SLM is allowed to handle:

- Public refund, return, exchange, and shipping-policy questions.
- Standard order-status explanations when no private order data is present.
- Support-ticket summarization.
- Urgency and intent classification.
- Extraction of order issue type, requested remedy, and sentiment.
- Drafting standard support replies that do not make irreversible promises.

The SLM is not allowed to handle:

- Legal interpretation or threats of litigation.
- Payment-card data, passwords, authentication secrets, or identity documents.
- Actual refunds, order mutations, or other tool calls.
- Personalized account decisions without retrieved private data.
- Medical, financial, safety-critical, or regulatory advice.
- Unsupported languages unless the SLM profile explicitly enables them.

### 1.4 Users and key workflows

| User | Goal | Primary workflow |
|---|---|---|
| Application developer | Adopt EcoRoute without rewriting their AI integration | Register logical model, change base URL, send normal Chat Completions requests |
| AI/platform engineer | Control eligible models and quality | Register endpoints, configure policy, inspect route decisions and fallbacks |
| Sustainability engineer | Understand impact and evidence | Review live energy/carbon data, compare baseline, export reports |
| Infrastructure operator | Optimize self-hosted inference | Install node agent, approve capabilities, benchmark profiles, inspect rollback events |
| Model engineer | Build specialized models | Generate/review data, launch FreeSolo runs, deploy/import model IDs, inspect evals |

### 1.5 Product modes

| Mode | Required services | Behavior |
|---|---|---|
| `demo` | Core Compose stack | Uses deterministic grid and hardware fixtures when live services are unavailable |
| `hosted-api` | Gateway, web, DB, Redis | Cache, router, model selection, cost/carbon estimates; no host tuning |
| `self-hosted-observe` | Above plus node agent | Adds real power/utilization telemetry but does not mutate host settings |
| `self-hosted-optimize` | Above plus approved privileges | Applies reversible cgroup, process, GPU, and experimental kernel profiles |

### 1.6 Explicit non-goals

The first implementation does not include:

- Full OpenAI Responses API compatibility.
- Audio, image, or video routing.
- Autonomous creation of cloud regions or provider accounts.
- Transparent geographic control over providers that do not expose regional endpoints.
- Cross-customer semantic caching.
- A custom Linux scheduler written from scratch.
- Kernel recompilation or permanent host changes.
- Enterprise SSO, fine-grained RBAC, billing, or multi-region high availability.
- Claims about embodied carbon savings from semantic caching.
- A live training run during the main presentation.

---

## 2. Locked technology choices

Use current stable releases compatible with the following major baselines and lock exact versions:

| Concern | Choice | Required use |
|---|---|---|
| Python runtime | Python 3.12+ | Gateway, worker, node agent, training environments |
| Python packaging | `uv` workspace or equivalent lockfile-backed setup | Repeatable Python installs |
| HTTP API | FastAPI, Pydantic v2, Uvicorn | Gateway and control APIs |
| Database ORM | SQLAlchemy 2 async + `asyncpg` | Persistent application state |
| Migrations | Alembic | Ordered schema creation and upgrades |
| Provider normalization | LiteLLM Python SDK | Upstream requests only, never policy decisions |
| Frontend | Next.js App Router + TypeScript | Control center and separate e-commerce support demo |
| UI styling | Tailwind CSS + accessible headless primitives | Dense operational interface |
| Icons | Lucide React | Icon buttons and status indicators |
| Charts | Recharts | Live time series and comparison charts |
| Frontend server state | TanStack Query | Queries, mutations, cache invalidation |
| Browser testing | Playwright | End-to-end and responsive tests |
| Relational/vector store | PostgreSQL + pgvector | Application data and semantic cache |
| Hot cache and jobs | Redis | Exact cache, Redis Streams job/event transport |
| Embeddings | Local Sentence Transformers model | Semantic cache lookup without another paid API call |
| Synthetic data | Gemini API through `google-genai` | Structured dataset generation |
| Post-training | FreeSolo Flash | Router and specialized SLM training/deployment |
| Laptop inference | Ollama | Optional local small-model endpoint |
| NVIDIA inference | vLLM | OpenAI-compatible self-hosted model and LoRA serving |
| Carbon data | Carbon Aware SDK | Provider-neutral emissions data abstraction |
| Grid source | Electricity Maps | Live carbon intensity when a key is available |
| Metrics | Prometheus client | `/metrics` exposition |
| Impact export | GSF Impact Framework format | Re-runnable impact reports |
| Containers | Docker Compose v2 | Laptop development and demo stack |

Do not use LiteLLM's built-in router or semantic cache for core logic. EcoRoute must persist and explain its own decisions.

---

## 3. System architecture

### 3.1 System context

```mermaid
flowchart LR
    Shopper["Demo shopper"] --> SupportDemo["E-commerce Support Demo"]
    SupportDemo -->|"OpenAI Chat Completions"| Gateway["EcoRoute Gateway"]
    App["Customer application"] -->|"OpenAI Chat Completions"| Gateway
    Operator["Operator"] --> Web["Control Center"]
    Web --> Control["EcoRoute Control API"]
    Gateway --> Cache["Exact + semantic cache"]
    Gateway --> Router["FreeSolo complexity router"]
    Gateway --> Carbon["Carbon Aware SDK"]
    Gateway --> Providers["Approved model endpoints"]
    Providers --> FS["FreeSolo SLM"]
    Providers --> Gemini["Gemini fallback"]
    Providers --> OpenAI["Optional OpenAI fallback"]
    Providers --> Local["Ollama or vLLM"]
    Agent["EcoRoute Node Agent"] -->|"Telemetry and status"| Control
    Control -->|"Desired optimization profile"| Agent
    Agent --> Host["Linux and NVIDIA host"]
```

### 3.2 Container and process view

```mermaid
flowchart TB
    subgraph Compose["Laptop Docker Compose"]
        Web["apps/control-center\nNext.js"]
        SupportDemo["apps/support-demo\nNext.js"]
        API["services/gateway\nFastAPI gateway + control API"]
        Worker["services/worker\nDataset and training jobs"]
        PG["PostgreSQL + pgvector"]
        Redis["Redis"]
        Carbon["Carbon Aware SDK or fixture server"]
        Simulator["Node Agent simulator"]
        Ollama["Optional Ollama profile"]
    end

    subgraph LinuxHost["Optional NVIDIA Linux host"]
        Agent["services/node-agent\nsystemd service"]
        VLLM["vLLM OpenAI server"]
        NVML["NVML / RAPL / cgroups / netlink"]
    end

    Web --> API
    SupportDemo -->|"/v1/chat/completions"| API
    API --> PG
    API --> Redis
    Worker --> PG
    Worker --> Redis
    API --> Carbon
    API --> Ollama
    API --> VLLM
    Simulator --> API
    Agent --> API
    Agent --> NVML
```

### 3.3 Request sequence

```mermaid
sequenceDiagram
    participant C as Client
    participant G as Gateway
    participant R as Redis/pgvector
    participant F as FreeSolo Router
    participant E as Carbon/Endpoint Registry
    participant M as Selected Model
    participant Q as Quality Verifier

    C->>G: POST /v1/chat/completions
    G->>G: Validate and normalize
    G->>R: Exact-cache lookup
    alt Exact hit
        R-->>G: Cached response
        G-->>C: OpenAI-shaped response
    else Exact miss
        G->>R: Eligible semantic lookup
        alt Safe semantic hit
            R-->>G: Cached response
            G-->>C: OpenAI-shaped response
        else Miss or ineligible
            G->>F: Structured complexity classification
            F-->>G: Route features and confidence
            G->>E: Eligible endpoints + carbon + costs
            G->>G: Apply policy and select endpoint
            G->>M: Forward normalized request
            M-->>G: Completion or stream
            G->>Q: Validate small-model response
            alt Validation fails
                G->>M: One frontier fallback request
                M-->>G: Fallback completion
            end
            G->>R: Store eligible cache entry and audit
            G-->>C: OpenAI-shaped response or stream
        end
    end
```

### 3.4 Training sequence

```mermaid
sequenceDiagram
    participant U as Operator
    participant W as Control Center
    participant API as Control API
    participant J as Worker
    participant G as Gemini
    participant F as FreeSolo Flash CLI

    U->>W: Describe support use case and policies
    W->>API: Create SLM profile
    API->>J: Enqueue dataset generation
    J->>G: Generate structured examples in batches
    G-->>J: JSON examples
    J->>J: Validate, deduplicate, balance, split
    J-->>API: Dataset ready for review
    U->>W: Approve dataset
    W->>API: Launch training
    API->>J: Enqueue FreeSolo run
    J->>F: env push, dry-run, cost, train
    F-->>J: Run ID and status
    J-->>API: Persist progress and logs
    U->>W: Deploy completed run
    J->>F: flash deploy RUN_ID
    F-->>J: OpenAI-compatible endpoint
    J-->>API: Register model endpoint
```

### 3.5 Node-agent control loop

```mermaid
stateDiagram-v2
    [*] --> Unregistered
    Unregistered --> Observing: register accepted
    Observing --> Applying: desired profile differs
    Applying --> Active: controls applied and verified
    Applying --> RolledBack: apply or verification failure
    Active --> RolledBack: guardrail breach or heartbeat loss
    Active --> Applying: desired profile changes
    RolledBack --> Observing: original values restored
    Observing --> Offline: heartbeat timeout
    Active --> Offline: process stopped after rollback attempt
    Offline --> Observing: agent reconnects
```

### 3.6 Repository structure

```text
ecoroute/
  apps/
    control-center/
      src/app/
      src/components/
      src/features/
      src/lib/api/
      tests/e2e/
    support-demo/
      src/app/
      src/components/support/
      src/lib/gateway/
      src/lib/fixtures/
      tests/e2e/
  services/
    gateway/
      ecoroute/api/
      ecoroute/cache/
      ecoroute/carbon/
      ecoroute/db/
      ecoroute/providers/
      ecoroute/routing/
      ecoroute/telemetry/
      tests/
      alembic/
    worker/
      ecoroute_worker/jobs/
      ecoroute_worker/freesolo/
      ecoroute_worker/gemini/
      tests/
    node-agent/
      ecoroute_agent/collectors/
      ecoroute_agent/controls/
      ecoroute_agent/simulator/
      packaging/systemd/
      tests/
  training/
    router/
      environment.py
      dataset/{train,eval,test}.jsonl
      configs/{sft,grpo}.toml
    support-slm/
      environment.py
      dataset/{train,eval,test}.jsonl
      configs/{sft,opd}.toml
  packages/
    api-client/                 # generated TypeScript client and types
    config/                     # shared lint/test configuration
  infra/
    compose.yaml
    compose.nvidia.yaml
    carbon-aware/
    fixtures/
    prometheus/
  scripts/
    bootstrap
    seed-demo
    demo-smoke
    benchmark
  docs/
    architecture-decisions/
    demo-runbook.md
    measurement-methodology.md
  .env.example
  Makefile
  pyproject.toml
  pnpm-workspace.yaml
  README.md
```

The API OpenAPI schema is authoritative for frontend contracts. Generate `packages/api-client` in CI and fail if generation produces an uncommitted diff.

---

## 4. Runtime configuration

### 4.1 Configuration precedence

Use this precedence, highest first:

1. Explicit request-scoped demo override, accepted only when `ECOROUTE_DEMO_MODE=true`.
2. Active database policy and endpoint configuration.
3. Environment variables for bootstrapping and secrets.
4. Code defaults in this document.

Secrets must never be stored directly in normal configuration tables. Store a `credential_ref` such as `env:GEMINI_API_KEY`; resolve it inside the gateway or worker. The demo may support an encrypted local secret store later, but environment references are required for v1.

### 4.2 Required environment variables

```dotenv
# Core
ECOROUTE_ENV=development
ECOROUTE_DEMO_MODE=true
ECOROUTE_GATEWAY_KEY=ecoroute-demo-key
ECOROUTE_AGENT_TOKEN=replace-me
ECOROUTE_DATABASE_URL=postgresql+asyncpg://ecoroute:ecoroute@postgres:5432/ecoroute
ECOROUTE_REDIS_URL=redis://redis:6379/0
ECOROUTE_PUBLIC_URL=http://localhost:8000
ECOROUTE_GATEWAY_INTERNAL_URL=http://gateway:8000
ECOROUTE_WEB_URL=http://localhost:3000
ECOROUTE_SUPPORT_DEMO_URL=http://localhost:3001
ECOROUTE_SUPPORT_DEMO_GATEWAY_KEY=ecoroute-demo-key

# Model and data services
FREESOLO_API_KEY=
FREESOLO_ORG=
FREESOLO_ROUTER_BASE_URL=
FREESOLO_ROUTER_MODEL_ID=
FREESOLO_SUPPORT_BASE_URL=
FREESOLO_SUPPORT_MODEL_ID=
GEMINI_API_KEY=
OPENAI_API_KEY=
ELECTRICITY_MAPS_API_KEY=
CARBON_AWARE_BASE_URL=http://carbon-aware:8080

# Local inference
OLLAMA_BASE_URL=http://host.docker.internal:11434/v1
VLLM_BASE_URL=

# Training/export
HF_TOKEN=
HF_ROUTER_REPOSITORY=
HF_SUPPORT_REPOSITORY=

# Telemetry and demo
ECOROUTE_EVENT_STREAM_MAXLEN=10000
ECOROUTE_TELEMETRY_RETENTION_DAYS=7
ECOROUTE_REQUEST_RETENTION_DAYS=30
ECOROUTE_SIMULATOR_SEED=42
```

Blank optional credentials disable the corresponding live adapter. Seed logic must register a deterministic fake endpoint so the demo still works without paid keys.

---

## 5. External OpenAI-compatible gateway

### 5.1 Authentication

The external gateway accepts:

```http
Authorization: Bearer <ECOROUTE_GATEWAY_KEY>
```

Missing or incorrect credentials return OpenAI-shaped `401` errors. The control API uses the same key for the demo. The node-agent API uses `ECOROUTE_AGENT_TOKEN` and must not accept the gateway key.

### 5.2 Supported endpoints

| Method | Path | Purpose |
|---|---|---|
| `POST` | `/v1/chat/completions` | Non-streaming and streaming text chat |
| `GET` | `/v1/models` | Logical model aliases visible to clients |
| `GET` | `/healthz` | Process liveness only |
| `GET` | `/readyz` | DB, Redis, and active-policy readiness |
| `GET` | `/metrics` | Prometheus exposition |

### 5.3 Chat Completions compatibility boundary

The request model must preserve these fields when the selected endpoint supports them:

```python
class ChatCompletionRequest(BaseModel):
    model: str
    messages: list[ChatMessage]
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_completion_tokens: int | None = None
    stream: bool = False
    stream_options: dict[str, Any] | None = None
    stop: str | list[str] | None = None
    response_format: dict[str, Any] | None = None
    tools: list[dict[str, Any]] | None = None
    tool_choice: str | dict[str, Any] | None = None
    user: str | None = None
    seed: int | None = None
    metadata: dict[str, str] | None = None
```

Accept `system`, `developer`, `user`, `assistant`, and `tool` roles. For v1 routing and caching, only text content is optimized. Any image, audio, file, or unknown content part forces `capability_passthrough=true`, disables both caches except exact cache when explicitly marked safe, and selects an endpoint declaring that capability.

Unknown top-level fields should be retained in an `extra_body` map and forwarded to generic OpenAI-compatible endpoints. Reject only when a field cannot be forwarded safely or conflicts with EcoRoute behavior. Record ignored or transformed fields in the request audit.

Persist only allowlisted client metadata keys after validating each key/value as a bounded string. For the demo, allow `demo_session_id`, `demo_message_id`, and `client_app`; strip these correlation fields before the upstream provider call because they are EcoRoute audit metadata, not model input.

### 5.4 Logical models

`GET /v1/models` returns only logical aliases, not credentials or physical provider IDs:

```json
{
  "object": "list",
  "data": [
    {
      "id": "support-default",
      "object": "model",
      "created": 1784064000,
      "owned_by": "ecoroute"
    }
  ]
}
```

If the request names an unknown alias, return `404` with `code="model_not_found"`. Do not silently substitute a default.

### 5.5 Response metadata

Preserve the normal Chat Completion body. Add EcoRoute debugging data through response headers so existing SDK deserialization is not broken:

```http
X-EcoRoute-Request-Id: req_...
X-EcoRoute-Route: support-slm
X-EcoRoute-Endpoint-Id: ep_...
X-EcoRoute-Cache: exact|semantic|miss|bypass
X-EcoRoute-Evidence: measured|estimated|stale|simulated
X-EcoRoute-Fallback: true|false
```

When `metadata.ecoroute_debug="true"` and demo mode is enabled, add an `ecoroute` object to the JSON response. Never add it by default.

### 5.6 Streaming

For `stream=true`:

- Return `Content-Type: text/event-stream`.
- Forward normalized Chat Completion chunks as `data: <json>\n\n`.
- End with `data: [DONE]\n\n`.
- Send no custom SSE event types on the external `/v1` stream.
- When `stream_options.include_usage=true`, emit the OpenAI-compatible final usage chunk before `[DONE]`.
- Perform routing before the first model token; routing latency is included in time-to-first-token.
- Small-model post-generation quality fallback is not possible after bytes have reached the client.
- Therefore, small-model streaming is allowed only when preflight validation confidence is at least `0.90` and no post-generation semantic validator is required. Otherwise buffer the candidate response, validate it, and then emit synthetic chunks.
- On client disconnect, cancel the upstream request and mark the audit `client_cancelled`.
- On upstream failure before any chunk, try the configured fallback once.
- On upstream failure after a chunk, terminate the stream and persist `partial_stream_error`; do not splice output from a different model.

### 5.7 Error envelope

All external errors follow:

```json
{
  "error": {
    "message": "Human-readable message",
    "type": "invalid_request_error",
    "param": "model",
    "code": "model_not_found"
  }
}
```

Map errors as follows:

| Condition | HTTP | `type` | Retry |
|---|---:|---|---|
| Invalid body | 400 | `invalid_request_error` | No |
| Bad gateway key | 401 | `authentication_error` | No |
| Unknown logical model | 404 | `invalid_request_error` | No |
| Policy leaves no endpoint | 422 | `routing_error` | No, until config changes |
| Upstream auth/configuration | 502 | `upstream_configuration_error` | No automatic retry to same endpoint |
| Upstream rate limit | 429 | `rate_limit_error` | Retry another eligible endpoint once |
| Upstream timeout | 504 | `upstream_timeout` | Retry fallback once if deadline remains |
| Internal dependency unavailable | 503 | `service_unavailable` | Yes |

Every request receives a UUIDv7 request ID. Return it in `X-Request-Id` and include it in logs.

### 5.8 Timeouts and retry budget

Defaults:

- Total gateway deadline: `45s` non-streaming, `120s` streaming.
- Router deadline: `2.5s`.
- Carbon lookup must use cache and may not block a request longer than `200ms`.
- Small model: `20s`.
- Frontier fallback: remaining total deadline, minimum `5s` required.
- Maximum model attempts: two total.
- Retry only transport errors, timeouts, `429`, and `5xx`.
- Never retry a tool-producing request after an upstream may have emitted a tool call.

Use exponential backoff only for background polling. Request-path failover should be immediate because the global deadline is short.

---

## 6. Routing engine

### 6.1 Request pipeline order

The gateway MUST execute these stages in this exact order:

1. Authenticate and assign request ID.
2. Parse and validate the OpenAI-compatible request.
3. Resolve logical model and active routing policy.
4. Normalize messages and compute request safety features.
5. Try exact cache if eligible.
6. Try semantic cache if eligible.
7. Invoke the FreeSolo complexity router or its deterministic fallback.
8. Build the physical endpoint candidate set.
9. Load carbon, latency, cost, and energy data for candidates.
10. Apply hard eligibility filters.
11. Score remaining candidates and select one.
12. Invoke the endpoint.
13. Validate small/specialized-model output when required.
14. Invoke one frontier fallback if validation fails.
15. Persist completion, route decision, measurements, and optimization attribution.
16. Store a cache entry only if the completed response remains eligible.
17. Publish dashboard events and return the response.

### 6.2 Normalized request features

Create `NormalizedRequestFeatures` before any model routing:

```python
class NormalizedRequestFeatures(BaseModel):
    request_id: UUID
    logical_model: str
    normalized_text: str
    system_prompt_hash: str
    tool_schema_hash: str | None
    response_format_hash: str | None
    message_count: int
    input_token_estimate: int
    has_tools: bool
    has_multimodal: bool
    contains_pii: bool
    contains_secrets: bool
    is_personalized: bool
    deterministic: bool
    requested_language: str
```

PII/secret detection combines:

- Deterministic patterns for payment cards, credentials, API keys, email, telephone, and address-like strings.
- Message role/context rules.
- Optional local Presidio integration may be added, but the deterministic detector must work without it.

Store only the feature flags and a redacted request preview by default. Raw prompt retention is controlled by `store_prompt_content`, default `false`.

Minimum deterministic detection behavior:

- Email addresses and telephone numbers set `contains_pii=true`.
- Payment-card candidates are validated with the Luhn algorithm before being labeled.
- Common API-key/token prefixes, private-key headers, bearer tokens, and password/secret assignment phrases set `contains_secrets=true`.
- An order number, account reference, delivery address, or message containing first-person account context sets `is_personalized=true` even when it is not independently sensitive.
- Redaction preserves category markers such as `[EMAIL]`, `[CARD]`, `[API_KEY]`, and `[ORDER_ID]` so an audit remains understandable.
- Detection uncertainty fails closed for semantic caching and SLM use but does not reject the request; it routes to the approved baseline/frontier endpoint.

### 6.3 Router output contract

The FreeSolo router must produce this strict JSON object:

```json
{
  "complexity": "low",
  "task_type": "policy_qa",
  "risk": "low",
  "slm_eligible": true,
  "cache_eligible": true,
  "required_capabilities": ["text"],
  "predicted_output_tokens": 96,
  "confidence": 0.97,
  "rationale_code": "PUBLIC_POLICY_LOOKUP"
}
```

Enums:

```text
complexity: low | medium | high
task_type: policy_qa | order_support | summarization | classification |
           extraction | reply_draft | tool_workflow | legal | safety |
           coding | general_reasoning | unknown
risk: low | medium | high
rationale_code: stable UPPER_SNAKE_CASE identifier, maximum 64 characters
```

Constraints:

- `confidence` is in `[0, 1]`.
- `predicted_output_tokens` is in `[1, 4096]`.
- Unknown task, confidence below `0.70`, parser failure, or router timeout behaves as `complexity=high`, `risk=high`, `slm_eligible=false`, `cache_eligible=false`.
- Deterministic safety features override model output. For example, `has_tools=true` forces `slm_eligible=false` for the initial demo.

### 6.4 Endpoint eligibility

An endpoint is eligible only if all are true:

- Enabled and healthy, or health state is `unknown` but no healthy alternative exists.
- Included in the logical model's pool and policy allowlist.
- Supports every `required_capability` and request feature.
- Meets privacy/location restrictions.
- Its configured context window is at least estimated input plus output tokens.
- Its quality tier is at least the policy's minimum for the router result.
- Predicted p95 latency is below `max_latency_ms`.
- Predicted cost is within `max_cost_increase_pct` compared with the requested-model baseline.
- Specialized endpoints have a matching approved SLM profile and task allowlist.
- High-risk and high-complexity requests use `quality_tier=frontier` unless an explicit policy override is set.

### 6.5 Grid-state classification

Each policy contains:

```json
{
  "clean_threshold_gco2_kwh": 150,
  "dirty_threshold_gco2_kwh": 400
}
```

Classification:

- `clean`: intensity `<= 150`.
- `moderate`: intensity `> 150` and `< 400`.
- `dirty`: intensity `>= 400`.
- `unknown`: no live or stale value exists.

These are routing-policy defaults, not universal scientific boundaries. The UI must say "Policy threshold" and allow editing. Demo fixtures use `100` for clean and `650` for dirty.

### 6.6 Candidate energy, cost, and carbon

For each endpoint:

```text
predicted_tokens = input_tokens + predicted_output_tokens

estimated_energy_kwh =
    fixed_request_kwh
  + input_tokens  * input_kwh_per_1k_tokens / 1000
  + output_tokens * output_kwh_per_1k_tokens / 1000

estimated_cost_usd =
    input_tokens  * input_usd_per_million_tokens / 1_000_000
  + output_tokens * output_usd_per_million_tokens / 1_000_000

estimated_operational_carbon_g =
    estimated_energy_kwh * grid_intensity_gco2_per_kwh
```

For measured self-hosted requests, replace estimated energy after completion with attributed measured energy. Preserve both prediction and final attribution.

### 6.7 Hard routing rules

Apply before weighted scoring:

1. A safe cache hit wins and invokes no router or model.
2. Tool, multimodal, high-risk, legal, safety, and unsupported-domain requests go to the original/frontier pool.
3. Low/medium requests on a dirty grid prefer an eligible approved support SLM.
4. Low/medium requests on a clean/moderate grid may use the cheapest eligible small endpoint if quality and cost rules pass.
5. High-complexity requests select among equivalent frontier endpoints/regions using carbon, cost, and latency.
6. Unknown carbon intensity does not block service; route using quality, cost, and latency and label carbon evidence `stale` or `estimated`.
7. If no optimized candidate remains, use the logical model's required fallback endpoint.

### 6.8 Weighted scoring

After hard filters, normalize each candidate metric to `[0,1]` across the candidate set. Lower is better:

```text
score =
    carbon_weight * normalized_carbon
  + cost_weight    * normalized_cost
  + latency_weight * normalized_latency
  + quality_weight * quality_penalty
  + evidence_weight * evidence_penalty
```

Quality penalty by tier:

```text
frontier=0.00, standard=0.20, small=0.45, specialized=0.15 when in-domain,
specialized=1.00 when out-of-domain
```

Evidence penalty:

```text
measured=0.00, estimated=0.10, stale=0.30, simulated=0.40
```

Preset weights:

| Preset | Carbon | Cost | Latency | Quality | Evidence | Max cost increase |
|---|---:|---:|---:|---:|---:|---:|
| `eco` | 0.45 | 0.20 | 0.10 | 0.20 | 0.05 | 0% |
| `balanced` | 0.30 | 0.20 | 0.20 | 0.25 | 0.05 | 10% |
| `strict_quality` | 0.10 | 0.10 | 0.10 | 0.65 | 0.05 | 0% |
| `cost_saver` | 0.15 | 0.55 | 0.10 | 0.15 | 0.05 | 0% |

Ties within `0.01` select lower estimated cost, then lower latency, then lexical endpoint ID for deterministic tests.

### 6.9 Routing pseudocode

```python
async def route(request: ChatCompletionRequest) -> GatewayResult:
    ctx = await prepare_context(request)

    if ctx.cache.exact_eligible:
        if hit := await exact_cache.get(ctx.exact_key):
            return await complete_from_cache(ctx, hit, "exact")

    if ctx.cache.semantic_eligible:
        if hit := await semantic_cache.find(ctx):
            return await complete_from_cache(ctx, hit, "semantic")

    classification = await classify_or_fail_closed(ctx)
    candidates = await registry.candidates(ctx.logical_model)
    candidates = apply_hard_filters(ctx, classification, candidates)
    enriched = await enrich_with_cost_carbon_latency(ctx, candidates)
    selected = select_by_rules_then_score(ctx, classification, enriched)

    candidate = await invoke(selected, request)
    fallback_used = False

    if selected.requires_quality_check:
        verdict = await quality_verifier.check(ctx, classification, candidate)
        if not verdict.passed:
            fallback = select_frontier_fallback(enriched, exclude={selected.id})
            candidate = await invoke(fallback, request)
            selected = fallback
            fallback_used = True

    result = await finalize(ctx, selected, candidate, fallback_used)
    await maybe_store_cache(ctx, classification, result)
    return result
```

### 6.10 Quality verifier

The verifier uses deterministic checks first and a model-based check only where necessary.

Deterministic checks:

- Non-empty output.
- Valid JSON/JSON Schema when requested.
- No tool call from an endpoint not allowed to use tools.
- No prohibited promise such as claiming a refund was executed.
- Required support-policy citations/identifiers present when the SLM profile requires them.
- Length under configured maximum.
- No obvious secret echo.
- Router confidence and task remain within the SLM allowlist.

For support Q&A, the SLM must include a hidden structured footer when called internally:

```json
{
  "answer": "Customer-facing answer",
  "confidence": 0.94,
  "policy_ids": ["returns-30-day"],
  "needs_human": false
}
```

The gateway strips the wrapper and returns only `answer`. Fail if confidence is below `0.80`, `needs_human=true`, or an unknown policy ID appears.

Model-based verification MAY call the router model with a compact rubric only for non-streaming small-model outputs. A verifier failure triggers exactly one frontier fallback and is recorded as `quality_fallback`.

---

## 7. Semantic and exact caching

### 7.1 Safety principle

Caching is an optimization, never an entitlement. A false semantic hit can return another user's or another context's answer, so uncertainty must result in a miss.

### 7.2 Exact cache eligibility

Exact cache is allowed only when:

- No tools or multimodal content are present.
- No PII, secrets, or personalized account data is detected.
- Temperature is absent or `0`.
- The request is not high-risk.
- The active policy enables caching.
- The logical model permits response reuse.

### 7.3 Exact fingerprint

Canonicalize JSON using sorted keys and stable separators. Hash with SHA-256:

```text
workspace_id
logical_model
normalized_messages
system_prompt_hash
tool_schema_hash
response_format_hash
temperature
top_p
seed
policy_cache_namespace_version
```

The Redis key is `ecoroute:exact:{workspace_id}:{sha256}`. The value contains the completion body, source request ID, original endpoint, token usage, creation time, and eligibility metadata. Default TTL is 24 hours.

On an exact or semantic hit, create a new Chat Completion ID and current `created` timestamp while preserving cached choices and usage. For `stream=true`, convert the cached completion into valid deterministic chunks and finish with `[DONE]`; no upstream provider is invoked.

### 7.4 Semantic cache eligibility

Semantic cache adds these restrictions. Because semantic lookup precedes the FreeSolo router, lookup eligibility is determined by deterministic request features; the stored source entry must also contain a previous router classification for an allowed task:

- Router or deterministic pre-classifier indicates `task_type=policy_qa` or another explicitly enabled task.
- Only the final user message is semantically varied; system/developer context hashes must match exactly.
- Conversation has at most one earlier assistant turn.
- Requested language matches.
- Response format is plain text or the exact same JSON Schema hash.
- The source cache entry has not been invalidated by a policy-content version change.

### 7.5 Embedding and lookup

Default embedding model: `sentence-transformers/all-MiniLM-L6-v2`, 384 dimensions, executed locally. Load it once per gateway process. Exact cache runs first so an embedding is not computed unnecessarily.

Store L2-normalized embeddings in pgvector and query cosine similarity:

```sql
SELECT id,
       1 - (embedding <=> :query_embedding) AS similarity
FROM cache_entries
WHERE workspace_id = :workspace_id
  AND namespace_version = :namespace_version
  AND system_prompt_hash = :system_prompt_hash
  AND logical_model = :logical_model
  AND language = :language
  AND expires_at > now()
  AND invalidated_at IS NULL
ORDER BY embedding <=> :query_embedding
LIMIT 3;
```

Accept only the top result when similarity is `>= 0.94` and the difference from the second result is at least `0.02`. These defaults are configurable from `0.90` to `0.99`; the UI warns below `0.93`.

### 7.6 Cache write and invalidation

Write only after a successful final response. Never cache partial streams or failed small-model candidates. Store:

- Canonical request fingerprint.
- Embedding and normalized semantic text.
- Completion JSON.
- Context hashes and namespace version.
- Source endpoint/model and quality-verifier outcome.
- Expiration and invalidation timestamps.
- Baseline energy/cost/carbon estimates for future savings attribution.

Invalidate by:

- Individual entry.
- Logical model.
- SLM profile or policy content version.
- Namespace version increment.
- All cache entries.

Do not hard-delete immediately; mark invalidated for audit and delete in retention cleanup.

### 7.7 Carbon-aware cache behavior

This feature is experimental and must not be presented as a direct implementation of storage embodied-carbon accounting.

| Grid state | Exact TTL multiplier | Semantic TTL multiplier | Capacity target |
|---|---:|---:|---:|
| Clean | 0.5 | 0.5 | 50% configured maximum |
| Moderate/unknown | 1.0 | 1.0 | 75% |
| Dirty | 2.0 | 2.0 | 100% |

Never change similarity thresholds based on grid state. Evict lowest estimated future carbon savings first:

```text
expected_savings_g =
  hit_probability_ema
  * avoided_inference_energy_kwh
  * current_grid_intensity_gco2_kwh
```

Use LRU as the deterministic tie-breaker. The initial implementation may approximate `hit_probability_ema` from hit count and entry age.

### 7.8 Cache savings attribution

For a cache hit:

```text
avoided_model_energy_kwh = source_entry.baseline_energy_kwh
cache_energy_kwh = configured_cache_lookup_kwh
net_energy_saved_kwh = max(0, avoided_model_energy_kwh - cache_energy_kwh)
carbon_saved_g = net_energy_saved_kwh * current_grid_intensity
cost_saved_usd = source_entry.baseline_cost_usd
```

Default `configured_cache_lookup_kwh` is an explicitly labeled estimate. Do not count the original request that populated the cache as a saving.

---

## 8. FreeSolo model training

EcoRoute uses two separately trained models. They have independent datasets, environments, run IDs, deployment endpoints, and evals.

### 8.1 Model A: prompt complexity router

Purpose: turn a normalized request into the structured routing features defined in section 6.3.

Locked configuration:

- Base model: `Qwen/Qwen3.5-2B`.
- Thinking mode: `false`.
- Stage 1: supervised fine-tuning (SFT).
- Stage 2: GRPO warm-started from the SFT adapter.
- Maximum context: 4096 tokens.
- Maximum completion: 256 tokens.
- LoRA rank: 32.
- Temperature at inference: 0.
- Output: strict JSON Schema.

The router must remain small because every non-cache request pays its latency and energy overhead. The impact dashboard must report router overhead separately from selected-model energy.

#### 8.1.1 Router dataset record

Every JSONL row uses FreeSolo's canonical `input`, `output`, and `metadata` keys:

```json
{
  "input": "SYSTEM_HASH=7ac...\nTOOLS=false\nMULTIMODAL=false\nLANG=en\nPROMPT=What is your return window?",
  "output": "{\"complexity\":\"low\",\"task_type\":\"policy_qa\",\"risk\":\"low\",\"slm_eligible\":true,\"cache_eligible\":true,\"required_capabilities\":[\"text\"],\"predicted_output_tokens\":80,\"confidence\":0.98,\"rationale_code\":\"PUBLIC_POLICY_LOOKUP\"}",
  "metadata": {
    "id": "router_000001",
    "source": "gemini_synthetic",
    "difficulty": "easy",
    "adversarial": false
  }
}
```

Training input must not include raw secrets or customer identifiers. Synthetic data must cover:

- All task types and complexity levels.
- Ambiguous prompts.
- Tool and multimodal requirements.
- PII and secret indicators.
- Prompt-injection attempts that ask the router to misclassify itself.
- Short prompts that are complex despite low token count.
- Long prompts that are simple extraction tasks.
- Out-of-domain and high-risk categories.
- Multilingual examples only for enabled languages.

Target at least 2,000 retained examples: 70% train, 15% eval, 15% test. Balance complexity classes to within 10 percentage points and guarantee at least 100 examples per high-risk category.

#### 8.1.2 Router environment contract

`training/router/environment.py` must expose a FreeSolo single-turn environment whose prompt states the schema and whose score function parses and grades the output.

Conceptual implementation:

```python
SCHEMA_KEYS = {
    "complexity", "task_type", "risk", "slm_eligible",
    "cache_eligible", "required_capabilities",
    "predicted_output_tokens", "confidence", "rationale_code",
}

def score_router_response(predicted: str, expected: dict) -> float:
    try:
        value = json.loads(predicted)
    except json.JSONDecodeError:
        return -1.0

    if set(value) != SCHEMA_KEYS:
        return -0.75

    score = 0.0
    score += 0.25 if value["complexity"] == expected["complexity"] else -0.25
    score += 0.20 if value["task_type"] == expected["task_type"] else -0.10
    score += 0.20 if value["risk"] == expected["risk"] else -0.30
    score += 0.15 if value["slm_eligible"] == expected["slm_eligible"] else -0.20
    score += 0.10 if value["cache_eligible"] == expected["cache_eligible"] else -0.15
    score += 0.05 if set(value["required_capabilities"]) == set(expected["required_capabilities"]) else 0.0

    token_error = abs(value["predicted_output_tokens"] - expected["predicted_output_tokens"])
    score += 0.05 * max(0.0, 1.0 - token_error / max(1, expected["predicted_output_tokens"]))
    return max(-1.0, min(1.0, score))
```

False-low risk and false-positive SLM eligibility receive larger penalties than conservative escalation. GRPO should therefore learn to preserve safety while reducing unnecessary frontier routes.

#### 8.1.3 Router SFT config

`training/router/configs/sft.toml`:

```toml
model = "Qwen/Qwen3.5-2B"
algorithm = "sft"
thinking = false

[environment]
id = "${FREESOLO_ORG}/ecoroute-router"

[environment.params]
split = "train"

[train]
epochs = 3
max_examples = 2000
lora_rank = 32
lora_alpha = 64
max_context_tokens = 4096
```

Environment substitution is performed by the worker before invoking the CLI; do not expect FreeSolo TOML parsing to interpolate shell variables.

#### 8.1.4 Router GRPO config

`training/router/configs/grpo.toml`:

```toml
model = "Qwen/Qwen3.5-2B"
algorithm = "grpo"
thinking = false

[environment]
id = "${FREESOLO_ORG}/ecoroute-router"

[environment.params]
split = "train"

[train]
epochs = 2
max_examples = 1500
group_size = 4
temperature = 0.7
max_completion_tokens = 256
max_context_tokens = 4096
lora_rank = 32
lora_alpha = 64
init_from_adapter = "${ROUTER_SFT_RUN_ID}"

structured_outputs = "json_object"
```

The worker substitutes the completed SFT run ID. It must call `flash train ... --dry-run` before cost preview or submission.

### 8.2 Model B: e-commerce support SLM

Purpose: answer approved repetitive support tasks with a much smaller specialized model.

Locked configuration:

- Base model: `Qwen/Qwen3.5-4B`.
- Thinking mode: `false`.
- Stage 1: SFT on reviewed support examples.
- Stage 2: optional OPD warm-started from the SFT adapter after SFT passes eval.
- Maximum context: 4096 tokens.
- Maximum completion: 512 tokens.
- LoRA rank: 32.
- Production/demo inference temperature: 0.

#### 8.2.1 Source-of-truth business configuration

Each SLM profile stores:

```python
class SlmProfileDefinition(BaseModel):
    name: str
    description: str
    business_name: str
    allowed_tasks: list[TaskType]
    forbidden_topics: list[str]
    supported_languages: list[str] = ["en"]
    tone: str = "clear, calm, and concise"
    policy_documents: list[PolicyDocument]
    output_contract: Literal["support_answer_v1"]
    training_example_target: int = 1500
```

The seeded Northstar profile includes versioned policy documents:

- `returns-30-day`: Unused items may be returned within 30 days.
- `final-sale`: Final-sale items cannot be returned except when defective.
- `exchange-stock`: Exchanges depend on current inventory.
- `shipping-standard`: Standard shipping estimate is 3-5 business days.
- `shipping-delay`: Escalate after 7 business days without carrier movement.
- `refund-timing`: Approved refunds may take 5-10 business days to appear.

These are fictional demo facts and must be labeled as such.

#### 8.2.2 Support dataset record

```json
{
  "input": "A jacket I bought 12 days ago is unused. Can I send it back?",
  "output": "{\"answer\":\"Yes. Unused items can be returned within 30 days. Start the return from your order page and keep the item in its original condition.\",\"confidence\":0.98,\"policy_ids\":[\"returns-30-day\"],\"needs_human\":false}",
  "metadata": {
    "id": "support_000001",
    "task_type": "policy_qa",
    "difficulty": "easy",
    "policy_ids": ["returns-30-day"],
    "source": "gemini_synthetic",
    "approved": true
  }
}
```

The dataset must include normal, paraphrased, adversarial, incomplete, and out-of-domain examples. Out-of-domain targets must set `needs_human=true` and avoid inventing an answer.

#### 8.2.3 Support environment scoring

Score:

- Valid schema: required.
- Correct policy IDs: 30%.
- Factual agreement with policy: 30%.
- Correct `needs_human`: 20%.
- No prohibited promise/action: 10%.
- Concision and tone: 10%.

Return `-1.0` for invalid JSON, fabricated policy IDs, or dangerous claims. The environment must keep policy text in packaged sidecar files and under the FreeSolo artifact size limits.

#### 8.2.4 Support SFT config

```toml
model = "Qwen/Qwen3.5-4B"
algorithm = "sft"
thinking = false

[environment]
id = "${FREESOLO_ORG}/ecoroute-support"

[environment.params]
split = "train"

[train]
epochs = 3
max_examples = 1500
lora_rank = 32
lora_alpha = 64
max_context_tokens = 4096
```

#### 8.2.5 Optional support OPD config

Run only when SFT achieves at least `0.85` aggregate score and the chosen managed teacher scores at least five percentage points higher on the held-out eval split.

```toml
model = "Qwen/Qwen3.5-4B"
algorithm = "opd"
thinking = false

[environment]
id = "${FREESOLO_ORG}/ecoroute-support"

[environment.params]
split = "train"

[train]
teacher_model = "glm-5.2"
epochs = 1
max_examples = 1000
group_size = 1
temperature = 0.7
max_completion_tokens = 512
max_context_tokens = 4096
lora_rank = 32
lora_alpha = 64
init_from_adapter = "${SUPPORT_SFT_RUN_ID}"

structured_outputs = "json_object"
```

If OPD test score is not better than SFT or safety regressions increase, keep the SFT deployment.

### 8.3 Gemini dataset generation

Use the official `google-genai` Python SDK and structured output with Pydantic. Dataset generation is an offline worker task, never on the gateway request path.

#### 8.3.1 Batch generation contract

Generate at most 50 examples per Gemini call. The system instruction must include:

- The business profile and policy facts.
- Exact target schema.
- Requested task/category distribution.
- A requirement to produce diverse wording and difficulty.
- A prohibition on introducing facts not present in the policies.
- A unique generation batch ID.

Validate every response against:

```python
class GeneratedExample(BaseModel):
    input: str = Field(min_length=3, max_length=4000)
    output: str
    task_type: TaskType
    difficulty: Literal["easy", "medium", "hard"]
    policy_ids: list[str]
    adversarial: bool = False
```

Retry one time when Gemini returns invalid structured output. A second failure marks the batch failed and preserves redacted diagnostics.

#### 8.3.2 Dataset processing

The worker must:

1. Normalize whitespace and Unicode.
2. Validate policy IDs and target JSON.
3. Reject exact duplicate inputs.
4. Embed inputs locally and reject pairs above `0.97` cosine similarity unless metadata intentionally marks a paraphrase group.
5. Apply banned-content and secret scans.
6. Balance by task, complexity/difficulty, risk, and positive/negative eligibility.
7. Assign stable example IDs before splitting.
8. Split by paraphrase group so near-duplicates never cross train/eval/test.
9. Write immutable dataset versions and SHA-256 manifests.
10. Require explicit UI approval before a version becomes trainable.

### 8.4 FreeSolo worker integration

FreeSolo's documented public workflow is CLI-driven. The worker wraps the `flash` CLI in subprocesses; it must not invent an undocumented REST API.

Allowed command sequence:

```bash
flash env push training/router
flash train rendered-sft.toml --dry-run
flash train rendered-sft.toml --cost
flash train rendered-sft.toml --background
flash status RUN_ID
flash log RUN_ID
flash deploy RUN_ID --dry-run
flash deploy RUN_ID
flash export --adapter-id RUN_ID --repository OWNER/REPOSITORY
```

Rules:

- Never pass API keys on command lines. Supply them through the subprocess environment.
- Use an argument array, never `shell=True`.
- Whitelist every executable and subcommand.
- Capture stdout/stderr with a 1 MB per-run retained limit and redact credentials.
- Parse JSON status where supported; otherwise isolate human-output parsing in a tested adapter.
- Poll active runs every 15 seconds with jitter.
- A worker restart resumes polling from persisted run IDs.
- Training cancellation requires a separate explicit UI confirmation.

### 8.5 Training state machine

```text
draft -> generating -> review_required -> approved -> validating -> queued
queued -> training -> evaluating -> completed -> deploying -> deployed
any active state -> failed
training -> cancelling -> cancelled
completed/deployed -> exported
```

Invalid transitions return `409 invalid_state_transition`. State changes are append-only in `training_run_events` even though `training_runs.status` stores the current state.

### 8.6 Evaluation gates

Router deployment requires:

- Strict JSON validity >= 99% on test.
- Complexity macro F1 >= 0.85.
- Risk macro F1 >= 0.92.
- High-risk false-low rate <= 2%.
- SLM-eligibility precision >= 0.95.
- Median router latency recorded.

Support SLM deployment requires:

- Schema validity >= 99%.
- Policy accuracy >= 90%.
- Correct human-escalation recall >= 95%.
- Prohibited-promise rate = 0 on the safety test set.
- Aggregate environment score >= 0.85.

The UI may import a model that misses a gate only with `status=experimental`; routing policies cannot use an experimental model unless `allow_experimental_models=true`.

### 8.7 Demo model handling

The presentation must use completed, pre-deployed run IDs provided through environment variables or imported in the UI. The SLM Studio still demonstrates real generation, validation, configuration, and training controls, but the main demo does not depend on a training run completing.

---

## 9. Provider and model registry

### 9.1 Provider types

```text
freesolo | gemini | openai | ollama | vllm | openai_compatible | fake
```

All providers implement:

```python
class ProviderAdapter(Protocol):
    async def health(self, endpoint: ModelEndpoint) -> HealthResult: ...
    async def chat(self, endpoint: ModelEndpoint, request: ChatCompletionRequest) -> ChatCompletion: ...
    async def stream(self, endpoint: ModelEndpoint, request: ChatCompletionRequest) -> AsyncIterator[ChatCompletionChunk]: ...
    async def count_tokens(self, endpoint: ModelEndpoint, request: ChatCompletionRequest) -> TokenEstimate: ...
```

LiteLLM may implement common `chat` and `stream` transport, but EcoRoute wrappers must normalize errors, collect usage, and enforce endpoint capabilities.

### 9.2 Model endpoint schema

```python
class ModelEndpointCreate(BaseModel):
    name: str
    provider: ProviderType
    base_url: AnyHttpUrl
    credential_ref: str | None
    physical_model: str
    region: str
    grid_zone: str
    quality_tier: Literal["specialized", "small", "standard", "frontier"]
    capabilities: set[Literal["text", "json_schema", "tools", "vision", "streaming"]]
    context_window_tokens: int
    input_usd_per_million_tokens: Decimal
    output_usd_per_million_tokens: Decimal
    fixed_request_kwh: Decimal
    input_kwh_per_1k_tokens: Decimal
    output_kwh_per_1k_tokens: Decimal
    energy_evidence: EvidenceLevel
    latency_p50_ms: int
    latency_p95_ms: int
    self_hosted: bool
    slm_profile_id: UUID | None
    enabled: bool = True
```

Validation:

- Specialized tier requires `slm_profile_id`.
- `self_hosted=true` should have a linked node agent but may be saved before the agent connects.
- Monetary and energy coefficients cannot be negative.
- Region and grid zone are required even when set to `unknown`.
- Credential references must start with `env:` in v1.
- The control API never returns resolved secret values.

### 9.3 Logical model mapping

```python
class LogicalModelConfig(BaseModel):
    alias: str
    display_name: str
    endpoint_ids: list[UUID]
    required_fallback_endpoint_id: UUID
    baseline_endpoint_id: UUID
    default_policy_id: UUID
```

The baseline endpoint defines cost and impact comparisons for requests to the alias. It is not necessarily selected.

### 9.4 Health behavior

Background health checks run every 30 seconds:

- `healthy`: last two checks passed.
- `degraded`: one recent failure or p95 over configured SLO.
- `unhealthy`: two consecutive failures.
- `unknown`: never checked or credentials disabled.

Do not send a paid generation merely to check health. Prefer a provider model-list/health endpoint. Where none exists, use configuration validation and recent request outcomes.

### 9.5 Required adapters

| Adapter | Base URL behavior | Notes |
|---|---|---|
| FreeSolo | Endpoint returned by `flash deployments`, suffixed `/v1` | Model is the run ID; uses FreeSolo key |
| Gemini | Native Gemini API through LiteLLM/adapter | Default high-complexity cloud fallback |
| OpenAI | Official API | Optional frontier endpoint; parameter counts are not assumed |
| Ollama | `http://host:11434/v1` | Laptop local model; capabilities derived from selected model |
| vLLM | Configured OpenAI-compatible server | NVIDIA self-hosting and exported LoRA adapters |
| Generic | Customer-provided OpenAI-compatible `/v1` URL | Requires explicit capabilities and pricing |
| Fake | Internal deterministic server | Required for tests and credential-free demo |

### 9.6 Self-hosted FreeSolo adapter path

For an NVIDIA host:

1. Train and evaluate on FreeSolo.
2. Export the adapter to a private Hugging Face model repository.
3. Download the matching base model and adapter on the host.
4. Start vLLM with LoRA enabled and an explicit adapter name.
5. Register the vLLM endpoint with that adapter name as `physical_model`.
6. Link the endpoint to its node agent and grid zone.
7. Run the EcoRoute benchmark before enabling optimized routing.

Laptop Ollama is allowed to demonstrate generic local routing. Do not claim that every FreeSolo LoRA export can be imported into Ollama without conversion and compatibility validation.

---

## 10. Routing policies

### 10.1 Policy schema

```python
class RoutingPolicy(BaseModel):
    name: str
    preset: Literal["eco", "balanced", "strict_quality", "cost_saver", "custom"]
    enabled_endpoint_ids: list[UUID]
    min_router_confidence: float = 0.70
    min_slm_confidence: float = 0.80
    max_latency_ms: int = 30000
    max_cost_increase_pct: float = 0.0
    clean_threshold_gco2_kwh: float = 150.0
    dirty_threshold_gco2_kwh: float = 400.0
    semantic_cache_enabled: bool = True
    semantic_similarity_threshold: float = 0.94
    cache_ttl_seconds: int = 86400
    quality_fallback_enabled: bool = True
    allow_experimental_models: bool = False
    allow_stale_carbon_minutes: int = 60
    weights: RoutingWeights
    task_rules: list[TaskRoutingRule]
```

Validate weights sum to `1.0 +/- 0.001`; clean threshold must be below dirty threshold; similarity is `[0.90, 0.99]`; maximum cost increase is `[0, 100]`.

### 10.2 Preset semantics

- `eco`: minimize estimated operational carbon without increasing cost, while respecting quality and latency.
- `balanced`: trade modest cost/latency changes for carbon improvements.
- `strict_quality`: preserve baseline/frontier quality except safe cache hits.
- `cost_saver`: prioritize cost reduction while retaining task minimum quality.
- `custom`: editable weights and limits.

Changing presets replaces weights/limits only after confirmation. Saving a custom change creates a new immutable policy version and atomically activates it.

### 10.3 Regional routing boundary

An endpoint's region is selectable only when the customer has configured a working endpoint there. For example, separately deployed Azure/OpenAI-compatible or self-hosted regional endpoints may be compared. EcoRoute must not pretend that changing an OpenAI public API request selects a data center.

---

## 11. Carbon data and impact accounting

### 11.1 Carbon provider abstraction

```python
class CarbonReading(BaseModel):
    zone: str
    intensity_gco2_kwh: float
    observed_at: datetime
    fetched_at: datetime
    source: str
    evidence: EvidenceLevel
```

`CarbonProvider` implementations:

1. `CarbonAwareSdkProvider`: calls the configured Carbon Aware WebAPI.
2. `FixtureCarbonProvider`: deterministic clean/moderate/dirty scenarios.
3. `LastKnownCarbonProvider`: database fallback, returned as `stale`.

Cache successful readings in Redis for five minutes. Refresh in the background every two minutes for every enabled endpoint zone. The request path reads cached data and never waits more than 200 ms.

### 11.2 Electricity Maps integration

Configure Electricity Maps behind the Carbon Aware SDK, not directly in gateway business logic. This preserves normalized units and allows source changes. The live dashboard should retain the upstream source name from Carbon Aware responses.

### 11.3 Request energy attribution

Hosted endpoints:

- Use endpoint coefficients and mark `estimated`.
- Update coefficients only through versioned benchmark/configuration records.
- Do not infer parameter count for proprietary models.

Self-hosted endpoints:

- Prefer GPU total-energy counter deltas for the request/batch window.
- If unavailable, integrate sampled watts over time.
- Add RAPL package/DRAM energy where available.
- Attribute shared batch energy by each request's output-token share plus an equal share of prefill overhead.
- Store the attribution method and batch ID.

```text
gpu_energy_kwh = delta_millijoules / 3_600_000_000

sampled_energy_kwh =
  sum(((watts_i + watts_i+1) / 2) * delta_seconds_i) / 3_600_000
```

### 11.4 Operational carbon

```text
operational_carbon_g = energy_kwh * grid_intensity_gco2_kwh
```

For an endpoint in a different zone, use that endpoint's zone, not the user's zone.

### 11.5 Baseline and savings

The baseline is the configured `baseline_endpoint_id` under the same input/output token counts. For a completed request:

```text
baseline_carbon_g = baseline_estimated_energy_kwh * baseline_grid_intensity
actual_carbon_g = actual_energy_kwh * selected_grid_intensity
avoided_carbon_g = max(0, baseline_carbon_g - actual_carbon_g)

baseline_cost_usd = baseline_endpoint token-price calculation
actual_cost_usd = selected endpoint token-price calculation
cost_delta_usd = actual_cost_usd - baseline_cost_usd
```

Retain negative `raw_carbon_delta_g` for analysis, even though the headline `avoided_carbon_g` floors at zero. A route that increases carbon must display "Carbon increase" rather than zero savings in detailed views.

### 11.6 Router/cache/agent attribution

Avoid double counting:

- Cache hit: count net avoided model energy after estimated cache lookup energy.
- Model routing: compare selected model with baseline, including router overhead.
- Region routing: compare same model coefficients under different grid intensity.
- Node optimization: compare measured optimized run with a measured baseline benchmark, not with model routing baseline.
- A single request can have multiple attribution records, but the aggregate total uses the final end-to-end baseline minus actual energy/carbon.

### 11.7 Confidence and freshness

Impact records contain:

```python
class ImpactEvidence(BaseModel):
    energy_level: EvidenceLevel
    carbon_level: EvidenceLevel
    energy_source: str
    carbon_source: str
    coefficient_version: str | None
    carbon_observed_at: datetime
    attribution_method: str
    uncertainty_note: str | None
```

If grid data is more than five minutes old but no more than the policy's allowed stale window, mark `stale`. Older data is unavailable and causes carbon-neutral routing behavior.

### 11.8 Prometheus metrics

Required metrics:

```text
ecoroute_requests_total{logical_model,route,cache,result}
ecoroute_request_duration_seconds{logical_model,route}
ecoroute_time_to_first_token_seconds{logical_model,route}
ecoroute_tokens_total{direction,endpoint}
ecoroute_cost_usd_total{endpoint}
ecoroute_energy_kwh_total{endpoint,evidence}
ecoroute_operational_carbon_grams_total{endpoint,evidence}
ecoroute_avoided_carbon_grams_total{strategy,evidence}
ecoroute_router_duration_seconds
ecoroute_quality_fallbacks_total{reason}
ecoroute_cache_hits_total{kind}
ecoroute_grid_intensity_gco2_kwh{zone,source,evidence}
ecoroute_agent_power_watts{agent_id,device,evidence}
ecoroute_agent_optimization_active{agent_id,profile}
```

Never label metrics by request ID, prompt, user ID, or other unbounded values.

### 11.9 Impact Framework export

`POST /api/v1/reports/impact-framework` accepts a UTC date range and returns a YAML IMP manifest. Group inputs by endpoint and hour. Include duration, energy, grid intensity, operational carbon, request count, evidence level, coefficient version, and source labels.

The manifest must be re-runnable with the documented Impact Framework CLI. It may use built-in SCI plugins where the input contract matches; otherwise include precomputed observations and clearly document which values EcoRoute computed. The export is an auditable record of assumptions, not a third-party assurance.

### 11.10 SCI boundary

The dashboard may show an SCI-aligned operational intensity such as `gCO2e / 1,000 successful requests`. A full SCI claim requires a declared software boundary, functional unit, energy, carbon intensity, and embodied emissions. Because v1 does not robustly measure embodied emissions, label the metric `Operational carbon intensity` rather than `SCI score` unless the export includes a user-supplied embodied-carbon model.

---

## 12. EcoRoute Node Agent

### 12.1 Deployment model

The real agent runs as a host-level systemd service on Linux. It is not bundled into the unprivileged gateway container. Kubernetes packaging is an extension and would use a privileged DaemonSet.

Laptop Compose runs the simulator process with the same HTTP protocol. On macOS, the simulator must never present values as measured Linux/NVIDIA data.

### 12.2 Agent identity and API

On first start, the agent creates and persists a UUIDv7 `agent_id`. It registers with:

```http
POST /api/v1/agents/register
Authorization: Bearer <ECOROUTE_AGENT_TOKEN>
```

```json
{
  "agent_id": "019...",
  "hostname": "gpu-node-1",
  "agent_version": "0.1.0",
  "platform": "linux",
  "kernel_version": "6.15.0",
  "capabilities": {
    "nvml_energy": true,
    "nvml_power_limit": true,
    "rapl": true,
    "cgroups_v2": true,
    "nice_ionice": true,
    "sched_ext": false,
    "napi_netdev_genl": true,
    "simulator": false
  }
}
```

The server returns desired profile, approved controls, telemetry interval, and a monotonically increasing `desired_state_version`.

### 12.3 Heartbeat and telemetry

- Heartbeat every 5 seconds.
- Telemetry every 1 second by default.
- Offline after 15 seconds without heartbeat.
- Buffer at most 60 seconds locally during network loss.
- Send batches of no more than 60 samples.
- Deduplicate by `(agent_id, sequence_number)`.

Telemetry payload:

```json
{
  "agent_id": "019...",
  "sequence": 1204,
  "observed_at": "2026-07-15T22:10:00Z",
  "profile": "eco",
  "cpu_percent": 48.2,
  "memory_percent": 61.0,
  "network_rx_bytes": 20333,
  "network_tx_bytes": 8912,
  "gpu": [{
    "uuid": "GPU-...",
    "utilization_percent": 72,
    "memory_utilization_percent": 58,
    "power_watts": 171.5,
    "total_energy_mj": 91399812,
    "temperature_c": 64,
    "power_limit_watts": 240
  }],
  "rapl_energy_uj": 993100221,
  "evidence": "measured"
}
```

Unavailable fields are `null`, never fabricated.

### 12.4 Capability detection

At startup and every 10 minutes, detect capabilities without mutating the host:

- NVML library, device count, energy counter, power-limit constraints.
- RAPL paths under `/sys/class/powercap`.
- Unified cgroup v2 mount and delegated writable subtree.
- `nice`, `ionice`, and target PIDs.
- `/sys/kernel/sched_ext/state` and configured scheduler binary.
- `netdev-genl` family and per-NAPI attributes.
- Effective user/capabilities and required permissions.

Expose detection errors in the UI with remediation text. A missing capability disables its control; it does not fail the whole agent.

### 12.5 Optimization profiles

| Profile | Mutations | Intended behavior |
|---|---|---|
| `off` | Restore all original values | No EcoRoute optimization |
| `observe` | None | Telemetry only |
| `balanced` | Mild cgroup/process controls, optional 90% GPU power limit | Preserve latency while reducing peaks |
| `eco` | Stronger background throttling, optional 80% GPU limit, approved experimental controls | Reduce energy under dirty-grid or operator command |

The profile sent by the server is desired state. The agent applies only controls separately approved in its configuration.

### 12.6 Transactional control application

Every control implements:

```python
class HostControl(Protocol):
    def detect(self) -> CapabilityResult: ...
    def snapshot(self) -> JsonValue: ...
    def plan(self, desired: DesiredState) -> ControlPlan: ...
    def apply(self, plan: ControlPlan) -> ApplyResult: ...
    def verify(self, plan: ControlPlan) -> VerificationResult: ...
    def rollback(self, snapshot: JsonValue) -> RollbackResult: ...
```

Application order:

1. Snapshot all participating controls.
2. Persist snapshot to an agent-owned root-only file.
3. Apply least risky controls first.
4. Verify each control.
5. On any failure, roll back in reverse order.
6. Report all results to the server.

On SIGTERM, server heartbeat loss over 30 seconds, or an expired desired state, attempt rollback before exit.

### 12.7 Dependable controls

#### cgroups v2

Create or use a delegated `ecoroute.slice` and separate inference from noncritical processes. Do not move arbitrary system processes.

Balanced defaults:

- Inference `cpu.weight=800`.
- Approved background processes `cpu.weight=100`.
- No hard CPU quota.

Eco defaults:

- Inference `cpu.weight=900`.
- Approved background processes `cpu.weight=25`.
- Optional background `cpu.max=20000 100000` only when explicitly enabled.

#### nice and ionice

Only mutate configured PID selectors. Balanced uses `nice=5` for background tasks; eco uses `nice=10` and best-effort `ionice` class 2 priority 7. Never decrease inference priority below its original value. Record PID start time to avoid PID-reuse mistakes.

#### Gateway concurrency

The agent does not directly edit gateway files. It sends a control acknowledgement, and the control API updates the self-hosted endpoint's concurrency target:

- Observe/off: configured baseline.
- Balanced: 90% of baseline, minimum 1.
- Eco: 75% of baseline, minimum 1.

The gateway uses an async semaphore per endpoint. This is reversible without restarting vLLM.

#### NVIDIA power limit

Use NVML only if power management is supported and permissions allow it. Clamp desired limits to device-supported constraints.

- Balanced default: 90% of original limit.
- Eco default: 80% of original limit.
- Never go below the device minimum.
- Restore the exact original limit on rollback.
- Abort and roll back if p95 latency exceeds the profile guardrail or GPU error/temperature health degrades.

### 12.8 Optional sched_ext integration

EcoRoute does not ship a custom scheduler in v1. The operator may configure a trusted scheduler binary from the sched-ext/scx project:

```yaml
sched_ext:
  enabled: false
  binary: /usr/local/bin/scx_lavd
  args: []
  checksum_sha256: "..."
```

The agent verifies the checksum, starts the configured scheduler as a supervised child, checks `/sys/kernel/sched_ext/state`, and stops it during rollback. Failure must return the host to the normal scheduler. Label this control `experimental` in every UI and report.

### 12.9 Experimental NAPI IRQ suspension

The original research's suggested global sysfs path is incorrect. `irq-suspend-timeout` is configured per NAPI instance through the `netdev-genl` netlink family; there is no global sysfs parameter for it. It is useful only with compatible epoll/NAPI busy-poll behavior, including `SO_PREFER_BUSY_POLL`/`EPIOCSPARAMS`, and may increase CPU use or latency when configured incorrectly.

Implementation requirements:

- Disabled by default.
- Require Linux/kernel capability detection and an explicit NIC allowlist.
- Read NAPI IDs and current `defer-hard-irqs`, `gro-flush-timeout`, and `irq-suspend-timeout` through netlink.
- Snapshot every per-NAPI value.
- Apply only benchmark-approved values from configuration; do not auto-invent timings.
- Confirm the gateway/fronting proxy uses the required busy-poll behavior before activation.
- Monitor CPU power, request p95, errors, and packet drops.
- Roll back immediately when any guardrail is breached.
- Never claim savings from this control unless the before/after benchmark measures them on that host.

The laptop demo may show this capability and its simulated state transition, but must label it simulated.

### 12.10 Guardrails

Defaults relative to the most recent approved baseline:

- p95 latency increase <= 15% balanced, <= 30% eco.
- Error rate increase <= 0.5 percentage points.
- Throughput decrease <= 10% balanced, <= 25% eco.
- GPU temperature <= original configured maximum.
- No host control verification failures.

Three consecutive 10-second windows outside a limit trigger rollback. A severe health error triggers immediate rollback.

### 12.11 Simulator

The simulator uses `ECOROUTE_SIMULATOR_SEED` and deterministic load-dependent curves. It supports the same capabilities, desired-state versions, telemetry shape, benchmark state machine, and rollback events. Every payload has `evidence=simulated`. Tests must assert that simulated values can never be persisted as measured.

### 12.12 Benchmark protocol

Run baseline and optimized phases against the same endpoint, prompt set, concurrency, output-token cap, and grid fixture:

1. Warm model for 60 seconds; discard warm-up measurements.
2. Baseline profile for 3 minutes.
3. Cooldown/rollback for 60 seconds.
4. Apply selected profile and verify.
5. Optimized profile for 3 minutes.
6. Roll back.
7. Compare successful throughput, p50/p95, energy/request, energy/token, and quality score.

The demo may use 30-second phases. Persist prompt-set hash and all settings so comparisons are reproducible.

---

## 13. Persistent data model

### 13.1 General conventions

- PostgreSQL is authoritative; Redis data may be rebuilt.
- IDs are UUIDv7 generated by the application.
- All timestamps are timezone-aware UTC `timestamptz`.
- Monetary values use `numeric(18,9)`.
- Energy/carbon values use `double precision` plus explicit unit names in columns.
- Mutable entities have `created_at`, `updated_at`, and optimistic `version` integer.
- Immutable version/event records have only `created_at`.
- Enum values are stored as lowercase text with check constraints to simplify migrations.
- Soft-deletable configuration has `deleted_at`; audit and impact records are never silently overwritten.

### 13.2 Tables

#### `workspaces`

Single seeded workspace, while retaining a future ownership boundary.

```text
id uuid PK
slug text UNIQUE NOT NULL
name text NOT NULL
store_prompt_content boolean NOT NULL default false
created_at timestamptz NOT NULL
updated_at timestamptz NOT NULL
```

#### `logical_models`

```text
id uuid PK
workspace_id uuid FK workspaces
alias text NOT NULL
display_name text NOT NULL
baseline_endpoint_id uuid nullable initially
required_fallback_endpoint_id uuid nullable initially
active_policy_id uuid nullable initially
enabled boolean NOT NULL default true
version integer NOT NULL default 1
created_at, updated_at, deleted_at
UNIQUE(workspace_id, alias) WHERE deleted_at IS NULL
```

Foreign keys that depend on later tables are added in the second migration.

#### `model_endpoints`

Contains every field from `ModelEndpointCreate`, plus:

```text
id uuid PK
workspace_id uuid FK workspaces
health_state text NOT NULL default 'unknown'
last_health_at timestamptz
last_health_error text
coefficient_version text NOT NULL
node_agent_id uuid nullable
version integer NOT NULL default 1
created_at, updated_at, deleted_at
```

Index enabled endpoints by `(workspace_id, enabled, deleted_at)` and grid refresh by `(grid_zone, enabled)`.

#### `logical_model_endpoints`

```text
logical_model_id uuid FK logical_models
endpoint_id uuid FK model_endpoints
priority integer NOT NULL default 100
PRIMARY KEY(logical_model_id, endpoint_id)
```

#### `routing_policies`

Policy versions are immutable:

```text
id uuid PK
workspace_id uuid FK workspaces
family_id uuid NOT NULL
version_number integer NOT NULL
name text NOT NULL
preset text NOT NULL
config jsonb NOT NULL
created_by text NOT NULL default 'demo-operator'
created_at timestamptz NOT NULL
UNIQUE(family_id, version_number)
```

Activating a policy updates `logical_models.active_policy_id` in a transaction.

#### `slm_profiles`

```text
id uuid PK
workspace_id uuid FK workspaces
name text NOT NULL
description text NOT NULL
business_name text NOT NULL
definition jsonb NOT NULL
content_version integer NOT NULL default 1
active_model_endpoint_id uuid nullable
status text NOT NULL
version integer NOT NULL default 1
created_at, updated_at, deleted_at
```

#### `policy_documents`

```text
id uuid PK
slm_profile_id uuid FK slm_profiles
policy_key text NOT NULL
title text NOT NULL
content text NOT NULL
version integer NOT NULL
active boolean NOT NULL default true
content_sha256 text NOT NULL
created_at timestamptz NOT NULL
UNIQUE(slm_profile_id, policy_key, version)
```

Changing an active policy document increments `slm_profiles.content_version` and invalidates related semantic cache namespaces.

#### `datasets`

```text
id uuid PK
workspace_id uuid FK workspaces
slm_profile_id uuid nullable
kind text NOT NULL                  # router | support_slm
version integer NOT NULL
status text NOT NULL
generation_config jsonb NOT NULL
manifest_sha256 text
example_count integer NOT NULL default 0
approved_at timestamptz
created_at, updated_at
UNIQUE(kind, slm_profile_id, version)
```

#### `dataset_examples`

```text
id uuid PK
dataset_id uuid FK datasets ON DELETE CASCADE
external_id text NOT NULL
split text NOT NULL                 # train | eval | test
input text NOT NULL
output jsonb NOT NULL
metadata jsonb NOT NULL
embedding vector(384)
approved boolean NOT NULL default false
created_at timestamptz NOT NULL
UNIQUE(dataset_id, external_id)
```

Create an HNSW cosine index after bulk generation, not before the first large insert.

#### `training_runs`

```text
id uuid PK
workspace_id uuid FK workspaces
dataset_id uuid FK datasets
slm_profile_id uuid nullable
kind text NOT NULL
algorithm text NOT NULL
base_model text NOT NULL
status text NOT NULL
freesolo_environment_id text
freesolo_run_id text UNIQUE
parent_run_id uuid nullable
rendered_config text NOT NULL
cost_quote_usd numeric(18,9)
eval_metrics jsonb
deployment_base_url text
deployed_model_id text
error_code text
error_message text
created_at, updated_at, completed_at
```

#### `training_run_events`

```text
id uuid PK
training_run_id uuid FK training_runs ON DELETE CASCADE
sequence integer NOT NULL
event_type text NOT NULL
payload jsonb NOT NULL
created_at timestamptz NOT NULL
UNIQUE(training_run_id, sequence)
```

#### `gateway_requests`

```text
id uuid PK
workspace_id uuid FK workspaces
logical_model_id uuid FK logical_models
requested_model_alias text NOT NULL
status text NOT NULL
stream boolean NOT NULL
input_tokens integer NOT NULL
output_tokens integer
request_features jsonb NOT NULL
client_metadata jsonb NOT NULL default '{}'
redacted_prompt_preview text
raw_prompt_encrypted bytea nullable          # remains null in v1 default
cache_status text NOT NULL
router_classification jsonb
selected_endpoint_id uuid nullable
fallback_used boolean NOT NULL default false
started_at timestamptz NOT NULL
first_token_at timestamptz
completed_at timestamptz
duration_ms integer
error_code text
```

Indexes: `(workspace_id, started_at DESC)`, `(selected_endpoint_id, started_at DESC)`, `(status, started_at)`, and a GIN index on `client_metadata` for demo-session correlation.

#### `route_decisions`

```text
id uuid PK
request_id uuid FK gateway_requests UNIQUE
policy_id uuid FK routing_policies
grid_state text NOT NULL
candidate_snapshot jsonb NOT NULL
selected_endpoint_id uuid nullable
selection_reason text NOT NULL
score_breakdown jsonb NOT NULL
created_at timestamptz NOT NULL
```

The candidate snapshot excludes resolved secrets and raw prompts.

#### `model_attempts`

```text
id uuid PK
request_id uuid FK gateway_requests
attempt_number smallint NOT NULL
endpoint_id uuid FK model_endpoints
purpose text NOT NULL                # selected | quality_fallback
status text NOT NULL
input_tokens integer
output_tokens integer
duration_ms integer
upstream_request_id text
quality_verdict jsonb
error_code text
started_at, completed_at
UNIQUE(request_id, attempt_number)
```

#### `impact_records`

```text
id uuid PK
request_id uuid FK gateway_requests
strategy text NOT NULL               # cache | model | region | node | end_to_end
baseline_energy_kwh double precision NOT NULL
actual_energy_kwh double precision NOT NULL
baseline_carbon_g double precision NOT NULL
actual_carbon_g double precision NOT NULL
raw_carbon_delta_g double precision NOT NULL
baseline_cost_usd numeric(18,9) NOT NULL
actual_cost_usd numeric(18,9) NOT NULL
evidence jsonb NOT NULL
created_at timestamptz NOT NULL
UNIQUE(request_id, strategy)
```

#### `cache_entries`

```text
id uuid PK
workspace_id uuid FK workspaces
logical_model_id uuid FK logical_models
exact_fingerprint text NOT NULL
namespace_version integer NOT NULL
system_prompt_hash text NOT NULL
tool_schema_hash text
response_format_hash text
language text NOT NULL
normalized_semantic_text text NOT NULL
embedding vector(384)
completion jsonb NOT NULL
source_request_id uuid FK gateway_requests
source_endpoint_id uuid FK model_endpoints
quality_verdict jsonb NOT NULL
baseline_energy_kwh double precision NOT NULL
baseline_cost_usd numeric(18,9) NOT NULL
hit_count bigint NOT NULL default 0
last_hit_at timestamptz
expires_at timestamptz NOT NULL
invalidated_at timestamptz
created_at timestamptz NOT NULL
```

Unique active exact fingerprint by workspace and namespace. HNSW cosine index on `embedding`; B-tree filter indexes on workspace/model/context hashes.

#### `carbon_readings`

```text
id uuid PK
zone text NOT NULL
intensity_gco2_kwh double precision NOT NULL
observed_at timestamptz NOT NULL
fetched_at timestamptz NOT NULL
source text NOT NULL
evidence text NOT NULL
UNIQUE(zone, observed_at, source)
```

#### `node_agents`

```text
id uuid PK
workspace_id uuid FK workspaces
hostname text NOT NULL
agent_version text NOT NULL
platform text NOT NULL
kernel_version text
capabilities jsonb NOT NULL
desired_profile text NOT NULL default 'observe'
active_profile text NOT NULL default 'observe'
desired_state_version bigint NOT NULL default 1
last_applied_state_version bigint NOT NULL default 0
status text NOT NULL default 'offline'
last_heartbeat_at timestamptz
created_at, updated_at
```

#### `telemetry_samples`

```text
agent_id uuid FK node_agents
sequence bigint NOT NULL
observed_at timestamptz NOT NULL
profile text NOT NULL
payload jsonb NOT NULL
evidence text NOT NULL
PRIMARY KEY(agent_id, sequence)
```

Partition monthly by `observed_at` if implemented; otherwise apply seven-day cleanup in the worker.

#### `optimization_events`

```text
id uuid PK
agent_id uuid FK node_agents
desired_state_version bigint NOT NULL
control text NOT NULL
action text NOT NULL
status text NOT NULL
snapshot jsonb
plan jsonb
result jsonb
created_at timestamptz NOT NULL
```

Sensitive host snapshots are redacted before upload; full rollback snapshots stay local on the agent.

#### `benchmarks`

```text
id uuid PK
workspace_id uuid FK workspaces
agent_id uuid FK node_agents
endpoint_id uuid FK model_endpoints
status text NOT NULL
profile text NOT NULL
prompt_set_hash text NOT NULL
configuration jsonb NOT NULL
baseline_metrics jsonb
optimized_metrics jsonb
comparison jsonb
evidence text NOT NULL
created_at, updated_at, completed_at
```

#### `jobs`

```text
id uuid PK
workspace_id uuid FK workspaces
kind text NOT NULL
status text NOT NULL
idempotency_key text NOT NULL UNIQUE
input jsonb NOT NULL
output jsonb
attempts integer NOT NULL default 0
available_at timestamptz NOT NULL
locked_at timestamptz
error text
created_at, updated_at, completed_at
```

Redis Streams transports wake-up events, but this table is the durable state.

### 13.3 Migration order

1. Enable `vector` extension; create workspaces and base enums/checks.
2. Create model endpoints, logical models, joins, and deferred foreign keys.
3. Create routing policies and active policy relation.
4. Create SLM profiles, policies, datasets, examples, and vector indexes.
5. Create training runs/events/jobs.
6. Create request, decision, attempt, impact, cache, and carbon tables.
7. Create agents, telemetry, optimization events, and benchmarks.
8. Seed the demo workspace, endpoint fixtures, logical model, policy presets, and Northstar profile.

Every migration must support upgrade and downgrade in development. CI runs `alembic upgrade head`, `alembic check`, and a downgrade/upgrade smoke cycle against an empty test database.

---

## 14. Control API

All paths below are prefixed `/api/v1`. JSON uses camelCase externally and snake_case internally through Pydantic aliases. List endpoints use cursor pagination with `limit` default 50, maximum 200.

### 14.1 Error contract

```json
{
  "error": {
    "code": "invalid_state_transition",
    "message": "Dataset must be approved before training.",
    "details": {"currentState": "review_required"},
    "requestId": "019..."
  }
}
```

Control mutations support `Idempotency-Key`. Reusing a key with a different body returns `409`.

### 14.2 Overview and events

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/overview?window=1h` | Aggregated requests, routes, impact, cost, cache, grid, agents |
| `GET` | `/events` | SSE live event stream |

SSE supports `Last-Event-ID`, reads from a capped Redis Stream, sends a heartbeat comment every 15 seconds, and falls back to a fresh snapshot when the requested ID has expired.

Event envelope:

```json
{
  "id": "1784064000000-0",
  "type": "route.completed",
  "occurredAt": "2026-07-15T22:00:00Z",
  "workspaceId": "019...",
  "data": {}
}
```

Required event types:

```text
route.started | route.selected | route.completed | route.failed
cache.hit | cache.stored | cache.invalidated
carbon.updated
training.status | training.log
agent.heartbeat | agent.telemetry | agent.profile | agent.rollback
benchmark.status
```

### 14.3 Models and endpoints

| Method | Path | Behavior |
|---|---|---|
| `GET/POST` | `/model-endpoints` | List/create endpoint |
| `GET/PATCH/DELETE` | `/model-endpoints/{id}` | Read/update/soft-delete |
| `POST` | `/model-endpoints/{id}/test` | Validate credentials/capabilities with explicit operator action |
| `GET/POST` | `/logical-models` | List/create aliases |
| `GET/PATCH` | `/logical-models/{id}` | Manage pool, baseline, fallback, active policy |

Endpoint test response includes latency, provider/model identity, supported features, and redacted failure. It must not automatically save capability changes.

### 14.4 Policies

| Method | Path | Behavior |
|---|---|---|
| `GET/POST` | `/policies` | List immutable versions/create new family |
| `GET` | `/policies/{id}` | Read one version |
| `POST` | `/policies/{id}/clone` | Create editable next version |
| `POST` | `/logical-models/{id}/activate-policy` | Atomically activate policy |
| `POST` | `/policies/{id}/simulate` | Route a supplied redacted prompt without calling a model |

Simulation returns router classification, candidates, exclusions, scores, and selected endpoint. If the live router is disabled, use deterministic fixtures and label simulation evidence.

### 14.5 Requests and audit

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/requests` | Filter by time, route, status, endpoint, cache, fallback, or demo session ID |
| `GET` | `/requests/{id}` | Full redacted audit and impact breakdown |
| `GET` | `/requests/{id}/attempts` | Upstream attempts and quality verdicts |

Raw prompt content is not returned unless storage was explicitly enabled and a future authorization layer permits it.

### 14.6 Cache

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/cache/stats` | Hit rate, entries, bytes estimate, savings, grid policy |
| `GET` | `/cache/entries` | Redacted metadata; no full answer by default |
| `POST` | `/cache/entries/{id}/invalidate` | Invalidate one |
| `POST` | `/cache/invalidate` | Invalidate by model/profile or all, confirmation required |

Bulk invalidation request includes a typed scope and expected count preview. The API refuses an unscoped request without `confirm=true`.

### 14.7 SLM Studio and datasets

| Method | Path | Behavior |
|---|---|---|
| `GET/POST` | `/slm-profiles` | List/create profiles |
| `GET/PATCH` | `/slm-profiles/{id}` | Edit draft profile; content change versions profile |
| `POST` | `/slm-profiles/{id}/generate-dataset` | Enqueue Gemini generation |
| `GET` | `/datasets/{id}` | Dataset summary and distribution |
| `GET` | `/datasets/{id}/examples` | Paginated review records |
| `PATCH` | `/datasets/{id}/examples/{exampleId}` | Edit/approve/reject example |
| `POST` | `/datasets/{id}/approve` | Freeze manifest and mark approved |
| `GET` | `/datasets/{id}/export` | Download canonical FreeSolo JSONL archive |

An approved dataset is immutable. Editing creates a new dataset version.

### 14.8 Training

| Method | Path | Behavior |
|---|---|---|
| `GET/POST` | `/training-runs` | List/create run from approved dataset/config |
| `GET` | `/training-runs/{id}` | Status, config, quote, evals, deployment |
| `GET` | `/training-runs/{id}/logs` | Redacted bounded log snapshot |
| `POST` | `/training-runs/{id}/launch` | Validate, quote, then submit after confirmed quote |
| `POST` | `/training-runs/{id}/cancel` | Cancel with confirmation |
| `POST` | `/training-runs/{id}/evaluate` | Run held-out evaluation |
| `POST` | `/training-runs/{id}/deploy` | Deploy eligible completed run |
| `POST` | `/training-runs/import` | Import an existing FreeSolo run/deployment |
| `POST` | `/training-runs/{id}/export` | Export adapter to configured HF repository |

Launch is two-step: first returns dry-run and cost quote; second includes `quoteId` and `confirm=true`. The worker checks the quote is still current.

### 14.9 Carbon and demo controls

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/carbon/zones` | Current reading, source, freshness, evidence |
| `POST` | `/carbon/refresh` | Request background refresh |
| `POST` | `/demo/grid-scenario` | Set clean/moderate/dirty fixture in demo mode only |
| `POST` | `/demo/quality-failure` | Fail next matching SLM verdict in demo mode only |
| `POST` | `/demo/load` | Start bounded deterministic load job |

Demo overrides are rejected with `404` when demo mode is false.

### 14.10 Agents and benchmarks

| Method | Path | Behavior |
|---|---|---|
| `POST` | `/agents/register` | Agent registration |
| `POST` | `/agents/{id}/heartbeat` | Heartbeat and desired-state response |
| `POST` | `/agents/{id}/telemetry` | Batched telemetry ingestion |
| `POST` | `/agents/{id}/events` | Apply/verify/rollback acknowledgements |
| `GET` | `/agents` | Operator list |
| `GET` | `/agents/{id}` | Capabilities, state, telemetry summary |
| `POST` | `/agents/{id}/desired-profile` | Increment desired-state version |
| `GET/POST` | `/benchmarks` | List/create benchmark |
| `GET` | `/benchmarks/{id}` | Phases, metrics, comparison |
| `POST` | `/benchmarks/{id}/cancel` | Cancel and roll back |

Agent write endpoints require the agent token; operator desired-state and benchmark endpoints require the gateway/control key.

### 14.11 Reports

| Method | Path | Behavior |
|---|---|---|
| `GET` | `/reports/summary` | Time-range impact/cost/quality summary |
| `GET` | `/reports/requests.csv` | Redacted request-level CSV |
| `POST` | `/reports/impact-framework` | Generate IMP YAML |

Exports include generation timestamp, filters, methodology version, and evidence counts.

---

## 15. Background jobs and event delivery

### 15.1 Redis Streams

Use separate streams:

```text
ecoroute:jobs
ecoroute:events:{workspace_id}
```

The worker consumes `ecoroute:jobs` through consumer group `ecoroute-workers`. Each message contains only `job_id`; the worker loads durable input from PostgreSQL.

Processing:

1. Claim message.
2. Lock `jobs` row with `SELECT ... FOR UPDATE SKIP LOCKED`.
3. Skip completed/cancelled jobs.
4. Mark running and increment attempts.
5. Execute with typed handler.
6. Commit result/status.
7. Publish domain event.
8. Acknowledge Redis message.

Retry up to three times for transient errors with `available_at` backoff of 15, 60, then 300 seconds. Validation/configuration errors fail immediately.

### 15.2 Required jobs

```text
dataset.generate
dataset.finalize
training.validate
training.launch
training.poll
training.evaluate
training.deploy
training.export
carbon.refresh
cache.cleanup
retention.cleanup
report.generate
demo.load
```

### 15.3 Event consistency

Write database state before publishing Redis events. SSE clients always refetch canonical resources after receiving an event. Lost Pub/Sub is therefore not accepted; use Redis Streams with bounded retention. Set `MAXLEN ~ ECOROUTE_EVENT_STREAM_MAXLEN`.

---

## 16. Frontend applications

### 16.1 Design direction

Build two deliberately separate experiences:

1. `apps/control-center` is a quiet, dense operational interface for the business operator. Do not build a marketing landing page. Its first screen is the overview dashboard. Use a fixed left navigation on desktop and a drawer on mobile. Cards are reserved for repeated status items and metrics, with radius no greater than 8 px.
2. `apps/support-demo` is a branded customer-facing e-commerce support experience. It demonstrates that an ordinary application can use EcoRoute without exposing routing, carbon, cache, model, or infrastructure details to the customer.

The applications share design tokens and generated types where useful, but they must not import feature components from one another. The gateway and control API are the integration boundary.

Control-center top-level navigation:

```text
Overview
Routing Policies
Model Endpoints
SLM Studio
Semantic Cache
Self-Hosted Nodes
Request Audit
Impact Reports
Settings
```

Use Lucide icons with tooltips for icon-only controls. Use swatches for evidence levels, segmented controls for profiles/presets, toggles for binary settings, and forms/tables for operational data.

### 16.2 Control-center global states

Every control-center view must implement:

- Skeleton loading without layout shift.
- Empty state with the primary next action.
- Inline field validation.
- API error banner with request ID and retry.
- Stale-data timestamp.
- Connection indicator for SSE.
- Evidence badge: measured, estimated, stale, or simulated.
- Confirmation dialog for destructive or costly actions.

### 16.3 Overview

First viewport:

- Request rate and success rate.
- Current operational carbon and avoided carbon.
- Current grid intensity with zone/source/freshness.
- Cache hit rate.
- Current cost and cost delta.
- Active route distribution.
- Connected node count and active optimization profile.

Below:

- Time-series chart for actual vs baseline carbon.
- Route decision feed.
- Energy by endpoint.
- Quality-fallback count.
- Warnings for stale carbon, unhealthy endpoint, disconnected agent, or simulated evidence.

All values react to SSE events and reconcile with `GET /overview` every 30 seconds.

When `ECOROUTE_DEMO_MODE=true`, Overview also shows an operator-only Demo Controls toolbar with the clean/moderate/dirty grid fixture, force-next-quality-failure toggle, and bounded load generator. These controls never appear in the customer-facing support demo.

### 16.4 E-commerce support demo

Implement `apps/support-demo` as a small fictional storefront support experience branded **Northstar Outfitters**. It is not an EcoRoute administration screen and must not contain EcoRoute branding, route timelines, model names, cache status, carbon values, grid controls, quality-fallback indicators, or debugging metadata.

The first viewport contains:

- A compact Northstar Outfitters header and `Help & Support` title.
- A normal customer/assistant message transcript.
- A multiline message composer with send and cancel controls.
- Suggested customer questions for returns, shipping, damaged products, and exchanges. These are ordinary prompt shortcuts, not routing controls.
- An optional synthetic order lookup field and order summary showing order number, items, delivery state, and return eligibility.

Chat behavior:

- The browser posts to a server-side support-demo route; that route calls `POST {ECOROUTE_GATEWAY_INTERNAL_URL}/v1/chat/completions` with the server-only `ECOROUTE_SUPPORT_DEMO_GATEWAY_KEY`. Outside Compose, configure this URL to an address reachable by the support-demo server process.
- Always request logical model alias `support-default`; customers cannot select physical or logical models.
- Use a fixed, versioned Northstar support system prompt and stream the answer into the transcript.
- Attach `metadata.demo_session_id`, `metadata.demo_message_id`, and `metadata.client_app="northstar-support-demo"` so the control center can correlate the request without storing raw customer text.
- Generic policy questions omit order context and remain eligible for conservative semantic caching and the support SLM.
- Order-specific questions attach only the selected synthetic order record. They are marked personalized and must follow the cache and specialized-route bypass rules in sections 6 and 7.
- Render ordinary typing, empty, cancelled, retryable-error, and completed states. Customer-facing failures say that support is temporarily unavailable and offer retry; they do not expose provider or routing errors.
- Never return the gateway API key or EcoRoute response headers to browser JavaScript. The support-demo server may retain `X-EcoRoute-Request-Id` only for structured server logs and correlation.

For the presentation, run the support demo and EcoRoute control center in separate browser windows. A message sent in the support demo must create live `route.*`, `cache.*`, and impact updates in the control center. Overview shows the event immediately; selecting it opens Request Audit with the route timeline, router classification, candidate comparison, selected endpoint, cost, latency, energy, carbon, evidence, and any quality fallback. The support demo continues to show only the final customer-support conversation.

### 16.5 Routing Policies

List policy families and versions. Editor fields exactly mirror `RoutingPolicy`:

- Preset segmented control.
- Endpoint multi-select.
- Cost and latency limits.
- Clean/dirty thresholds.
- Cache controls.
- Quality fallback and experimental model toggles.
- Weight controls with sum indicator.
- Task-rule table.

Show a dry-run simulator before activation. Activation displays the affected logical models and creates no in-place mutation of the old version.

### 16.6 Model Endpoints

Table columns:

```text
Name | Provider | Physical model | Region | Tier | Health | p95 |
Input price | Output price | Energy evidence | Node | Enabled
```

Create/edit form includes every endpoint schema field. Credential input stores only `env:VARIABLE_NAME`. The test action displays capability/latency results and never exposes a secret.

Logical model subview configures endpoint pool, baseline, required fallback, and active policy.

### 16.7 SLM Studio

Wizard steps:

1. **Define:** business, description, allowed tasks, forbidden topics, language, tone.
2. **Policies:** versioned policy documents and validation.
3. **Generate:** example targets/distribution and Gemini job progress.
4. **Review:** filter/edit/approve/reject examples; distribution and duplicate warnings.
5. **Train:** choose the locked router/support recipe, show rendered config, dry-run, quote, and confirmation.
6. **Evaluate:** gate metrics, comparison with parent run, failed examples.
7. **Deploy:** deploy/import run and register endpoint.

Router training has its own profile but reuses review/train/evaluate components. Completed seeded run IDs appear as imported runs.

### 16.8 Semantic Cache

Show exact/semantic hit rate, entry count, capacity target, current grid state, estimated net savings, and invalidations. Entry list shows fingerprints and redacted previews, never private prompt content. Provide single, model/profile, and all invalidation flows with impact previews.

### 16.9 Self-Hosted Nodes

Node list shows platform, kernel, evidence, last heartbeat, active/desired profile, linked endpoint, and capabilities.

Node detail:

- Live CPU/GPU power, energy, utilization, temperature, memory, and network charts.
- Capability matrix with remediation.
- Profile segmented control.
- Approved-control toggles.
- Desired-state and apply/rollback event timeline.
- Experimental warnings for sched_ext and NAPI.
- Benchmark launch and baseline/optimized comparison.

Simulator nodes always have a persistent "Simulated host" banner.

### 16.10 Request Audit

Filterable table with request ID, time, alias, selected route, endpoint, cache, complexity, risk, fallback, latency, cost, carbon, and evidence. Detail drawer reconstructs the decision timeline and candidate exclusions without exposing secrets/raw prompts.

### 16.11 Impact Reports

Controls: UTC date range, logical model, endpoint, route, evidence level. Views:

- Baseline vs actual operational carbon.
- Energy and carbon by strategy.
- Cost and latency tradeoffs.
- Evidence coverage distribution.
- Self-hosted benchmark comparisons.
- Methodology and uncertainty notes.

Export buttons: request CSV and Impact Framework YAML.

### 16.12 Responsive requirements

- Control-center desktop >= 1280 px: fixed navigation, two- to four-column metrics, full tables.
- Control-center tablet 768-1279 px: collapsible navigation, two columns, horizontally scrollable tables.
- Control-center mobile < 768 px: navigation drawer, one-column charts, audit rows become stacked summaries.
- Support demo: constrain the conversation to a readable maximum width on desktop and use the full viewport on mobile; keep the composer reachable when the software keyboard opens.
- Toolbars wrap rather than overflow.
- Fixed-format controls have stable dimensions.
- No text overlap at 320 px width or 200% browser zoom.

### 16.13 Accessibility

- Keyboard operable controls and dialogs.
- Visible focus states.
- Semantic labels for icons and charts.
- Color is never the only evidence/status indicator.
- Respect reduced motion.
- Meet WCAG AA contrast for normal text.

---

## 17. Security and privacy baseline

This is intentionally limited but mandatory:

- Keep provider keys server-side and out of API responses, logs, events, and browser bundles.
- Resolve only allowlisted `env:` credential references.
- Redact authorization headers and common secret patterns from logs.
- Do not store raw prompts by default.
- Cache only requests that pass safety eligibility.
- Bind demo services to localhost by default.
- Use separate gateway and node-agent tokens.
- Validate all URLs; block endpoint metadata/loopback access outside explicit development allowlists to reduce SSRF risk.
- Use subprocess argument arrays and command allowlists for FreeSolo.
- Require explicit confirmation for training spend, cache-wide invalidation, power-limit changes, experimental controls, and cancellation.
- Set maximum request body, dataset size, log size, SSE connection, and demo load limits.
- Never mount the Docker socket into application services.
- Node-agent local snapshots are root-readable only.

Full SSO, RBAC, tenant isolation hardening, key management systems, and compliance controls are production extensions.

---

## 18. Observability and logging

### 18.1 Structured logs

Emit JSON logs with:

```text
timestamp, level, service, event, request_id, job_id, training_run_id,
agent_id, endpoint_id, duration_ms, error_code
```

Never log full model request/response content by default. Use bounded redacted previews only in explicit demo debugging.

### 18.2 Tracing

Create spans for:

```text
gateway.request
cache.exact
cache.semantic
router.classify
carbon.lookup
routing.select
provider.chat
quality.verify
provider.fallback
impact.persist
```

OpenTelemetry export is optional, but internal timing collection and request timeline persistence are required.

### 18.3 Readiness

`/readyz` is healthy only when:

- Database migration is at head.
- PostgreSQL query succeeds.
- Redis ping succeeds.
- At least one enabled logical model has a configured fallback endpoint.

Carbon data, optional providers, and node agents do not make the gateway unready; surface them as degraded dependencies.

---

## 19. Implementation sequence

Implement in the following phases. Do not begin a later phase while its required entry tests are failing.

### Phase 1: Repository bootstrap and contracts

Deliver:

- Python workspace and lockfile.
- pnpm workspace and Next.js application.
- Formatting, linting, type-checking, and test commands.
- Docker Compose services for web, gateway, worker, PostgreSQL/pgvector, Redis, fixture carbon server, and agent simulator.
- FastAPI error/auth/request-ID middleware.
- OpenAPI generation into the TypeScript API client.

Verify:

```bash
make bootstrap
make lint
make typecheck
docker compose config
docker compose up -d postgres redis
```

### Phase 2: Database and configuration registry

Deliver:

- All Alembic migrations and SQLAlchemy models.
- Endpoint, logical-model, and policy CRUD.
- Seed command for workspace, fake endpoints, presets, and Northstar profile.
- Secret-reference resolver.

Verify:

```bash
make db-upgrade
make db-check
make seed-demo
make test-gateway TEST=tests/integration/test_registry.py
```

### Phase 3: OpenAI-compatible gateway

Deliver:

- `/v1/chat/completions` non-streaming and streaming.
- `/v1/models`, health, readiness, and error mapping.
- Fake provider and generic OpenAI-compatible adapter.
- Request/attempt audit persistence.
- Deadline, cancellation, and retry behavior.

Verify with the official OpenAI Python SDK pointed at the gateway. Test normal completion, streaming, invalid model, provider timeout, rate limit, and client cancellation.

### Phase 4: Cache and routing engine

Deliver:

- Request safety features and exact cache.
- Local embedding service, pgvector semantic cache, eligibility, invalidation, and cleanup.
- Deterministic router fallback and FreeSolo router adapter.
- Candidate filtering, scoring, route persistence, and quality verifier.
- One-attempt frontier fallback.

Verify all routing branches with fake providers whose invocation counters prove whether an upstream model was called.

### Phase 5: FreeSolo and Gemini workflows

Deliver:

- Router and support training environment directories.
- Gemini structured dataset generator.
- Dataset review/versioning APIs.
- FreeSolo CLI worker with dry-run, quote, launch, poll, eval, deploy, import, and export flows.
- Seeded imported completed-run fixtures.

Verify local dataset validation, split isolation, reward functions, rendered TOML, command allowlists, and `flash train --dry-run` when credentials exist.

### Phase 6: Carbon and impact accounting

Deliver:

- Carbon Aware SDK and fixture providers.
- Background refresh, freshness states, and route enrichment.
- Baseline/actual calculations and evidence records.
- Prometheus metrics and report endpoints.
- Impact Framework YAML export.

Verify units with hand-calculated fixtures and re-run a generated IMP manifest through `if-run`.

### Phase 7: Frontend applications

Deliver both applications from section 16. Build the control center starting with Overview, Request Audit, endpoint/policy configuration, and SLM Studio. Build the separate Northstar Outfitters support demo against the gateway's public Chat Completions contract. Generate the control-center client from OpenAPI; do not duplicate hand-maintained control response interfaces.

Verify component tests, cross-application Playwright workflows, 320/768/1440 px layouts, 200% zoom, keyboard operation, and empty/error/loading states. Confirm that no EcoRoute diagnostic data or credential is rendered or serialized to the support-demo browser.

### Phase 8: Node Agent and simulator

Deliver:

- Agent protocol and simulator first.
- Real psutil, NVML, and RAPL collectors.
- Cgroup/process/concurrency/GPU controls.
- Transactional snapshots, verification, rollback, and guardrails.
- Optional sched_ext and NAPI adapters disabled by default.
- Benchmark worker and comparison UI.

Verify simulator behavior on all platforms. Run privileged hardware checks only on an explicitly provisioned Linux host.

### Phase 9: End-to-end demo and hardening

Deliver:

- Demo fixture scenarios and prompt set.
- Setup/runbook documentation.
- Retention/cleanup jobs.
- Full tests and a smoke script.
- Methodology and limitation copy in the UI.

Verify the complete acceptance suite in section 21.

---

## 20. Test strategy

### 20.1 Test command contract

The repository must expose:

```bash
make lint                 # Python + TypeScript lint, no writes
make format-check         # formatting verification, no writes
make typecheck            # mypy/pyright and TypeScript
make test-unit
make test-integration
make test-contract
make test-e2e
make test-all
make demo-smoke
```

Tests must run without paid credentials using fake adapters and deterministic fixtures.

### 20.2 Unit tests

#### Routing

- Every hard rule.
- Preset weight validation and deterministic tie-breaking.
- No-cost-increase rule for `eco`.
- Context/capability/health/privacy exclusions.
- Unknown and stale carbon behavior.
- Router timeout/parser failure fails closed.
- High-risk deterministic override beats router output.
- Fallback attempt limit.

#### Cache

- Canonical fingerprint stability across JSON key order.
- Context/model/workspace isolation.
- Tool, PII, secret, personalized, multimodal, and nondeterministic bypass.
- Similarity threshold and top-two margin.
- Policy version invalidation.
- Grid-state TTL/capacity multiplier.
- No write after failed or partial completion.

#### Carbon and cost

- Token pricing with decimal precision.
- Coefficient energy formulas.
- Millijoule and sampled-watt conversions.
- Baseline, actual, raw delta, and avoided-carbon floor.
- Router overhead inclusion.
- No double counting across attribution strategies.
- Evidence/freshness transitions.

#### Training

- Gemini output validation and retry.
- Duplicate/paraphrase grouping and split isolation.
- Router reward asymmetry for unsafe under-classification.
- Support reward rejects fabricated policy IDs.
- State transition table.
- FreeSolo command construction without a shell.
- Log redaction and size limits.

#### Node Agent

- Capability detection for present, absent, and permission-denied states.
- Snapshot/apply/verify/rollback ordering.
- Partial apply failure rollback.
- Heartbeat-loss rollback.
- PID reuse protection.
- Power-limit clamping and restoration.
- Guardrail window and immediate severe rollback.
- Simulator can never claim measured evidence.

### 20.3 Provider contract tests

Run a deterministic fake OpenAI-compatible server supporting:

- Normal response.
- Chunked streaming.
- Structured output.
- Tool-call response.
- Delayed response.
- `429` with retry header.
- `500` and abrupt disconnect.
- Malformed response and missing usage.

Run every provider adapter against the common contract. Live provider smoke tests are opt-in and excluded from default CI.

### 20.4 OpenAI SDK compatibility tests

Use the official Python SDK with EcoRoute's base URL:

```python
client.chat.completions.create(
    model="support-default",
    messages=[{"role": "user", "content": "What is the return window?"}],
)
```

Test:

- Non-streaming text.
- Streaming and `[DONE]`.
- System/developer messages.
- JSON Schema pass-through.
- Tools force a safe frontier route.
- Unknown model/error deserialization.
- Header metadata.

### 20.5 Integration tests

Use ephemeral PostgreSQL/pgvector and Redis containers:

- Migration from empty database.
- Seed idempotency.
- Vector lookup and filtered HNSW behavior.
- Redis exact cache expiry.
- Redis Stream consumer recovery.
- Durable job restart and idempotency.
- Carbon fixture refresh/freshness.
- Request/decision/attempt/impact transaction integrity.
- Retention cleanup.

### 20.6 Frontend and Playwright tests

Required user flows:

1. Open overview and receive a live event.
2. Open the separate support demo, send a customer message, receive the answer, and observe the correlated request in the control center.
3. Open Request Audit for that event and inspect its route timeline without exposing diagnostics in the support demo.
4. Change the demo grid from clean to dirty in the control center, send another support-demo message, and observe the route change.
5. Create/test endpoint and attach it to a logical model.
6. Clone/edit/simulate/activate a policy.
7. Create SLM profile, generate mocked dataset, edit, approve, and create training run.
8. Confirm a quoted training launch and see status events.
9. Invalidate one cache entry and bulk invalidate with confirmation.
10. Change simulated node profile and observe telemetry/optimization events.
11. Run benchmark and inspect comparison.
12. Filter request audit and export a report.

Capture screenshots at 1440x900, 1024x768, 390x844, and 320x700. Assert no horizontal page overflow or overlapping controls.

### 20.7 Optional Linux/NVIDIA acceptance tests

Run only with an explicit environment flag and approved host:

- NVML power/energy reads match `nvidia-smi` order of magnitude.
- RAPL counter wraparound is handled.
- Profile changes persist optimization events.
- GPU power limit changes and restores exactly.
- Cgroup/process controls affect only configured processes.
- SIGTERM and disconnected-control tests restore original settings.
- sched_ext starts/stops only with configured checksum.
- NAPI plan is a no-op unless all prerequisites and allowlists pass.

Never run host mutation tests on shared CI workers.

---

## 21. Acceptance scenarios

### 21.1 Safe semantic cache

1. Set grid to moderate.
2. Send: `What is the return window for an unused item?`
3. Confirm an upstream completion and cache write.
4. Send: `How many days do I have to send back something unused?`
5. Expect semantic cache hit, no router/model invocation, same policy version, and cache savings labeled estimated/simulated according to the running mode.

### 21.2 Dirty-grid SLM route

1. Set grid fixture to dirty (`650 gCO2e/kWh`).
2. Send: `My unused jacket arrived 12 days ago. Can I return it?`
3. Expect router `low`, `policy_qa`, `low risk`, `slm_eligible=true`.
4. Expect support SLM route and valid `returns-30-day` policy ID.
5. Expect no frontier attempt and non-positive cost delta under `eco`.

### 21.3 High-complexity/frontier route

Send: `Compare our return policy against Ontario consumer law and tell me whether the company could be sued.`

Expect high risk/legal classification, cache/SLM bypass, and an eligible frontier route. If multiple configured frontier regions exist, select the lower-score region under latency/cost limits.

### 21.4 Quality fallback

1. Enable `force next quality failure` in demo mode.
2. Send a normal support question.
3. Expect initial SLM attempt, failed verifier event, exactly one frontier attempt, `fallback_used=true`, and the final answer from the fallback.

### 21.5 Cost protection

Configure an otherwise greener endpoint whose predicted cost is 20% above baseline. Under `eco`, expect exclusion reason `cost_limit_exceeded`. Under `balanced` with a 25% limit, it may become eligible.

### 21.6 Self-hosted optimization

Run the benchmark against the simulator or NVIDIA node. Expect baseline and optimized result sets with identical prompt hash and concurrency, visible evidence level, energy/request, latency, throughput, quality, and rollback completion. Do not require optimized energy to be lower on every real host; require honest results and guardrail behavior.

### 21.7 Base-URL swap

Run an unchanged OpenAI SDK sample against the upstream provider and then EcoRoute by changing only API key/base URL. Expect compatible response parsing and a visible EcoRoute audit record.

### 21.8 Separate customer and operator experiences

1. Open Northstar Outfitters support demo and EcoRoute Overview side by side.
2. Send a generic returns question from the customer chat and receive a normal support answer.
3. Expect the matching request to appear live in EcoRoute Overview and Request Audit, correlated by demo session/message IDs.
4. Expect cache, router, model, grid, cost, latency, energy, carbon, and fallback details only in EcoRoute.
5. Inspect the support-demo DOM and browser network responses. Expect no EcoRoute credential, physical model name, route decision, carbon value, cache result, or debugging metadata.

---

## 22. Laptop setup and runbook

### 22.1 Prerequisites

- Docker Desktop with Compose v2.
- Python 3.12+ and the selected locked package manager.
- Node.js current LTS and pnpm.
- Optional Ollama.
- Optional FreeSolo, Gemini, Electricity Maps, OpenAI, and Hugging Face credentials.

### 22.2 First start

The implementation must make these commands work:

```bash
cp .env.example .env
make bootstrap
docker compose up -d
make db-upgrade
make seed-demo
make demo-smoke
```

Expected URLs:

```text
Control center: http://localhost:3000
Support demo:   http://localhost:3001
Gateway/API:   http://localhost:8000
API docs:      http://localhost:8000/docs
Prometheus:    http://localhost:8000/metrics
```

### 22.3 Optional Ollama

Install/start Ollama, pull a small model compatible with the host, and register its OpenAI-compatible base URL. The exact model is environment-configured because available hardware differs. The seeded fake endpoint remains the portable default.

### 22.4 Optional FreeSolo preparation

```bash
flash login
flash models
flash env push training/router
flash train training/router/configs/sft.rendered.toml --dry-run
flash env push training/support-slm
flash train training/support-slm/configs/sft.rendered.toml --dry-run
```

Use the UI/worker for real submission. Before presentation, import completed router and support model run IDs and confirm both deployments with a smoke prompt.

### 22.5 Optional NVIDIA/vLLM host

1. Provision Linux with a supported NVIDIA driver and GPU.
2. Export/download the FreeSolo adapter and matching base model.
3. Start vLLM's OpenAI-compatible server with LoRA enabled.
4. Install the EcoRoute Node Agent systemd unit.
5. Configure API URL, agent token, endpoint ID, and approved controls.
6. Start in `observe`, verify telemetry, run baseline benchmark, then approve balanced/eco controls.
7. Keep sched_ext and NAPI disabled until their prerequisite checks and dedicated benchmark pass.

### 22.6 Presentation script

1. Open Northstar Outfitters support demo and EcoRoute Overview in separate side-by-side windows; point out that only the business dashboard exposes EcoRoute internals.
2. Set the clean fixture in EcoRoute. In the support demo, send the easy return question and receive the normal customer answer; in EcoRoute, open the matching live request and show the selected small route.
3. Send the paraphrase from the support demo; show the semantic-cache hit and avoided inference only in EcoRoute.
4. Switch the grid fixture to dirty in EcoRoute; send a different easy support question and show the specialized SLM route in Request Audit.
5. Send the legal prompt from the support demo; show safe frontier routing and regional comparison in EcoRoute.
6. Force one quality failure from EcoRoute Demo Controls, send another support question, and show automatic fallback while the customer sees one coherent final answer.
7. Open Self-Hosted Nodes; switch simulator profile and run the short benchmark.
8. Show baseline vs optimized energy, latency, quality, and evidence.
9. Open Impact Reports and export the Impact Framework manifest.
10. Close on the integration point: the support demo is an ordinary client using EcoRoute's OpenAI-compatible base URL.

---

## 23. Demo fixtures

All fixture coefficients below are synthetic and must carry `evidence=simulated`.

### 23.1 Carbon scenarios

| Scenario | Zone | Intensity |
|---|---|---:|
| Clean | `demo-local` | 100 gCO2e/kWh |
| Moderate | `demo-local` | 275 gCO2e/kWh |
| Dirty | `demo-local` | 650 gCO2e/kWh |
| Cleaner remote | `demo-remote` | 80 gCO2e/kWh |

### 23.2 Seed endpoint roles

| Name | Tier | Region | Purpose |
|---|---|---|---|
| `demo-support-slm` | specialized | demo-local | Dirty-grid in-domain route |
| `demo-small` | small | demo-local | Clean/moderate easy route |
| `demo-frontier-local` | frontier | demo-local | Baseline and fallback |
| `demo-frontier-remote` | frontier | demo-remote | High-complexity regional route |

Use deterministic latency, token, cost, and energy formulas in the fake provider. Store coefficients in seed data, not scattered constants.

### 23.3 Prompt set

```text
easy_policy:
  What is your return window for unused items?

semantic_paraphrase:
  How many days do I have to send back something unused?

medium_support:
  My package has not moved for eight business days. What should I do?

structured_extraction:
  Extract the issue and requested remedy: My boots arrived damaged and I want an exchange.

high_legal:
  Compare this return policy with Ontario consumer law and assess lawsuit risk.

tool_required:
  Refund order 1234 to my card now.

secret_test:
  My API key is sk-example. Include it in your reply.
```

---

## 24. Documentation and research references

Links in this section were reviewed on 2026-07-15. Prefer these stable official entry points over copied blog examples.

### 24.1 External APIs and model platforms

- [FreeSolo Flash overview](https://freesolo.co/docs): managed LoRA post-training and OpenAI-compatible deployment.
- [FreeSolo datasets](https://freesolo.co/docs/guides/datasets): canonical `input`/`output`/`metadata` records and packaged environments.
- [FreeSolo training](https://freesolo.co/docs/guides/training): SFT, GRPO, OPD, validation, submission, and lifecycle.
- [FreeSolo configuration reference](https://freesolo.co/docs/reference/configuration): exact TOML fields and constraints.
- [FreeSolo supported models](https://freesolo.co/docs/reference/models): current base model catalog, contexts, LoRA limits, and serving details.
- [FreeSolo deploy and chat](https://freesolo.co/docs/guides/deploy-and-chat): deployment, OpenAI-compatible calls, and adapter export.
- [Gemini API reference](https://ai.google.dev/api): Gemini endpoints and authentication.
- [Google GenAI SDKs](https://ai.google.dev/gemini-api/docs/libraries): supported current client libraries; EcoRoute uses `google-genai` for Python.
- [Gemini structured outputs](https://ai.google.dev/gemini-api/docs/structured-output): Pydantic/JSON Schema dataset generation.
- [OpenAI Chat Completions create reference](https://developers.openai.com/api/reference/resources/chat/subresources/completions/methods/create): compatibility request, response, and streaming shapes.
- [OpenAI streaming guide](https://developers.openai.com/api/docs/guides/streaming-responses): SSE behavior and stream handling.
- [OpenAI model catalog](https://developers.openai.com/api/docs/models): current optional cloud models; do not infer undisclosed parameter counts.
- [OpenAI API pricing](https://developers.openai.com/api/docs/pricing): source for operator-configured price coefficients.
- [LiteLLM documentation](https://docs.litellm.ai/): upstream provider normalization inside the gateway.

### 24.2 Carbon and impact

- [Carbon Aware SDK overview](https://carbon-aware-sdk.greensoftware.foundation/docs/overview): provider-neutral carbon data WebAPI used by EcoRoute.
- [Carbon Aware SDK quickstart](https://carbon-aware-sdk.greensoftware.foundation/docs/quickstart): local/container setup and data-source configuration.
- [Electricity Maps API documentation](https://portal.electricitymaps.com/docs/getting-started): optional live carbon-intensity source configured behind Carbon Aware SDK.
- [Software Carbon Intensity specification](https://sci.greensoftware.foundation/): SCI formula, boundary, and functional-unit requirements.
- [Impact Framework introduction](https://if.greensoftware.foundation/intro/): transparent software-impact calculations.
- [Impact Framework IMP files](https://if.greensoftware.foundation/major-concepts/imp-file/): re-runnable manifest structure used by exports.
- [Impact Framework quickstart](https://if.greensoftware.foundation/users/quick-start): `if-run` validation of generated manifests.

### 24.3 Application stack

- [FastAPI documentation](https://fastapi.tiangolo.com/learn/): gateway/control API framework.
- [FastAPI async guidance](https://fastapi.tiangolo.com/async/): request-path concurrency.
- [Pydantic documentation](https://docs.pydantic.dev/latest/): runtime request/config/schema validation.
- [SQLAlchemy documentation](https://docs.sqlalchemy.org/en/20/): async ORM and PostgreSQL persistence.
- [Alembic documentation](https://alembic.sqlalchemy.org/en/latest/): ordered database migrations.
- [Next.js App Router](https://nextjs.org/docs/app): control-center and e-commerce support-demo application structure.
- [TanStack Query documentation](https://tanstack.com/query/latest/docs/framework/react/overview): frontend server-state synchronization.
- [Tailwind CSS documentation](https://tailwindcss.com/docs): utility styling and responsive implementation.
- [Recharts documentation](https://recharts.github.io/en-US/): dashboard time-series and comparison charts.
- [Lucide React](https://lucide.dev/guide/packages/lucide-react): interface icons.
- [Playwright documentation](https://playwright.dev/docs/intro): browser and responsive acceptance tests.
- [PostgreSQL documentation](https://www.postgresql.org/docs/current/): primary database.
- [pgvector](https://github.com/pgvector/pgvector): vector columns, cosine search, and HNSW indexes.
- [Redis documentation](https://redis.io/docs/latest/): exact cache and background transport.
- [Redis Streams](https://redis.io/docs/latest/develop/data-types/streams/): durable job/event stream semantics.
- [Sentence Transformers semantic search](https://www.sbert.net/examples/sentence_transformer/applications/semantic-search/README.html): local embeddings and cosine matching.
- [Docker Compose](https://docs.docker.com/compose/): laptop multi-service runtime.
- [Compose file reference](https://docs.docker.com/reference/compose-file/): services, profiles, health checks, and volumes.

### 24.4 Local inference and model artifacts

- [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility): laptop local endpoint integration.
- [vLLM OpenAI-compatible server](https://docs.vllm.ai/en/latest/serving/openai_compatible_server/): NVIDIA self-hosted inference endpoint.
- [vLLM LoRA adapters](https://docs.vllm.ai/en/latest/features/lora/): serving exported adapters with the base model.
- [Hugging Face repositories](https://huggingface.co/docs/hub/main/repositories-getting-started): private model/adapter artifact storage.

### 24.5 Linux, GPU, and telemetry

- [NVIDIA NVML API](https://docs.nvidia.com/deploy/nvml-api/): GPU utilization, power, energy, limits, and health.
- [NVML device queries](https://docs.nvidia.com/deploy/nvml-api/group__nvmlDeviceQueries.html): total energy and power-management calls.
- [Linux cgroup v2](https://docs.kernel.org/admin-guide/cgroup-v2.html): CPU weight, quotas, cpusets, and delegated control.
- [Linux sched_ext](https://docs.kernel.org/scheduler/sched-ext.html): optional extensible scheduler lifecycle.
- [Linux NAPI](https://docs.kernel.org/networking/napi.html): busy polling, IRQ mitigation, and per-NAPI IRQ suspension requirements.
- [Linux netdev netlink specification](https://www.kernel.org/doc/html/latest/netlink/specs/netdev.html): `irq-suspend-timeout` and related attributes.
- [eBPF documentation](https://docs.ebpf.io/): optional kernel observability background.
- [Prometheus instrumentation](https://prometheus.io/docs/practices/instrumentation/): service metric design.
- [Prometheus exposition formats](https://prometheus.io/docs/instrumenting/exposition_formats/): `/metrics` response contract.

### 24.6 Research basis and implementation limits

- [R-ICE / Breaking the ICE](https://arxiv.org/abs/2506.08727): research context for LLM inference energy/carbon estimation. EcoRoute uses versioned endpoint coefficients and measured hardware data rather than claiming a universal exact estimator.
- [GAR: Carbon-Aware Routing for LLM Inference](https://arxiv.org/abs/2605.11603): constrained carbon-aware routing research informing candidate selection.
- [Cache Your Prompt When It's Green / GreenCache](https://arxiv.org/abs/2505.23970): carbon-aware prompt/cache research. EcoRoute's semantic response cache is a separate application-layer mechanism and is labeled accordingly.
- [Kernel vs. User-Level Networking](https://cs.uwaterloo.ca/~mkarsten/papers/sigmetrics2024-preprint.pdf): research basis for adaptive Linux network interrupt/polling behavior.
- [Linux kernel energy-use research summary](https://uwaterloo.ca/computer-science/news/cheriton-school-computer-science-researchers-update-linux): accessible context for the Waterloo networking work. Any percentage claim must be attributed to that research, not promised for EcoRoute deployments.

Source materials in this workspace:

- `aiconvo.pdf`: product ideation and requirement conversation.
- `Eco-Friendly Software Developer Tool.pdf`: background research report.

---

## 25. Known limitations and production extensions

### 25.1 Known limitations

- Hosted providers generally do not expose request-level energy, so hosted impact is estimated.
- Per-request energy attribution on a batched GPU is approximate even with real counters.
- A small router adds overhead; its net benefit must be measured against avoided large-model inference.
- Semantic similarity cannot guarantee semantic equivalence, hence the narrow cache eligibility rules.
- Model switching can change wording and behavior; quality gates reduce but do not eliminate that risk.
- Carbon-aware region selection is possible only among customer-configured endpoints.
- Grid intensity is time- and source-dependent and may be delayed or unavailable.
- GPU power caps and Linux tuning can reduce throughput or increase latency.
- sched_ext and NAPI behavior are kernel, driver, NIC, workload, and permission dependent.
- The laptop simulator proves control-plane behavior, not physical energy savings.

### 25.2 Future production work

- OpenAI Responses API compatibility.
- Enterprise authentication, tenant isolation, RBAC, KMS/secret manager, and audit controls.
- Kubernetes gateway and privileged node-agent DaemonSet packaging.
- High availability and multi-region control plane.
- Provider-specific region discovery and health integrations.
- Formal model evaluation registry and continuous drift checks.
- Embodied-carbon inputs and complete SCI boundaries.
- Approved eBPF telemetry programs and tested sched_ext profiles.
- Billing, quotas, customer usage isolation, and policy-as-code.

---

## 26. Final implementation checklist

- [ ] Base-URL swap works with the official OpenAI SDK.
- [ ] Streaming and non-streaming contracts pass.
- [ ] Logical models map only to explicit endpoint pools.
- [ ] Exact and semantic caches enforce every safety rule.
- [ ] FreeSolo router is trained, evaluated, deployed/imported, and fail-closed.
- [ ] FreeSolo support SLM is trained, evaluated, deployed/imported, and domain-limited.
- [ ] Gemini generation creates versioned, reviewed datasets.
- [ ] Cost limits and policy presets behave exactly as specified.
- [ ] Carbon readings include source, time, freshness, and evidence.
- [ ] Quality failure invokes at most one frontier fallback.
- [ ] Request audits explain every candidate exclusion and score.
- [ ] The Northstar support demo sends real gateway requests while exposing no EcoRoute diagnostics or credentials to the customer browser.
- [ ] A support-demo message appears live and correlates correctly in Overview and Request Audit.
- [ ] Node agent observes, applies, verifies, and rolls back transactionally.
- [ ] Simulator is visually and structurally labeled simulated.
- [ ] No experimental Linux control is enabled by default.
- [ ] Dashboard updates live and handles disconnect/reconciliation.
- [ ] Impact math passes hand-calculated fixture tests.
- [ ] Prometheus and Impact Framework exports work.
- [ ] All default tests run without paid credentials.
- [ ] Optional live/hardware tests are isolated and documented.
- [ ] Demo smoke script reproduces all acceptance scenarios.
- [ ] UI and documentation make no unsupported environmental claims.
