# EcoRoute AI Gateway

EcoRoute is a local-first, OpenAI Chat Completions-compatible AI gateway with a separate operator
control center and Northstar Outfitters support application. It implements conservative exact and
semantic response reuse, deterministic or FreeSOLO-backed request classification, constrained
carbon/cost/quality routing, transport and quality fallback, durable offline dataset/training jobs,
operational-impact accounting, and transactional self-hosted node controls.

The credential-free stack is fully runnable. It uses deterministic fake model endpoints, fixture
carbon readings, and a labeled node simulator. It never trains or spends money automatically.

Production mode supports live Electricity Maps v4 carbon intensity, explicit Carbon Aware
integration, and evidence-aware regional routing. See
[`docs/live-regional-routing.md`](docs/live-regional-routing.md) for configuration and claim
boundaries.
Gemini and FreeSOLO are configured but remain inactive until an operator supplies credentials and
explicitly starts the gated workflow.

## One-command stack

Requirements: Docker Desktop with Compose 2.20+ and `curl`. Host-side development and validation
commands additionally require Python 3.12+, Node 22+, and pnpm 9.12.3.

```bash
cp .env.example .env
docker compose up --build -d --wait
./scripts/demo-smoke
```

Compose automatically migrates and idempotently seeds PostgreSQL before starting the services.

- Control center: http://localhost:3000
- Northstar support demo: http://localhost:3001
- Gateway and control API: http://localhost:8000
- OpenAPI: http://localhost:8000/docs
- Prometheus metrics: http://localhost:8000/metrics

Use the gateway with an OpenAI SDK client configured with base URL
`http://localhost:8000/v1`, API key `ecoroute-demo-key`, and model `support-default`.

## Local validation

```bash
./scripts/bootstrap
make test-all
pnpm build
```

`scripts/bootstrap` creates `.venv` with Python 3.12 when needed, installs the pinned Python and
pnpm locks, and installs Playwright Chromium. Integration tests use disposable PostgreSQL,
pgvector, Redis, and Carbon Aware fixtures. Browser tests start the real gateway, worker, agent
simulator, and both Next.js applications.

Useful operations:

```bash
make compose-config
make generate-api
make impact-validate
docker compose --profile observability up -d prometheus
docker compose down
```

## Real Linux kernel-control demo

On an Apple-silicon Mac with at least 15 GB free, run the real cgroup-v2 benchmark in a Lima Ubuntu
VM:

```bash
./scripts/kernel-lab-up
./scripts/kernel-lab-demo
```

This path measures real Linux latency, throughput, process CPU time, cgroup application, PID
placement, throttling, and rollback. VM hardware energy remains explicitly unavailable. See the
[kernel lab runbook](docs/kernel-lab.md) for setup, evidence boundaries, and the demo claim.

The root `compose.yaml` includes the canonical `infra/compose.yaml`, so root commands and test
automation share one service definition instead of duplicated Compose files. `make
impact-validate` re-runs a generated export with the exactly pinned Impact Framework CLI.

## External artifacts intentionally not present

- `GEMINI_API_KEY` is blank, so live synthetic dataset generation cannot be exercised yet. The
  official `google-genai` structured-output adapter, durable job, review workflow, and reviewed
  manual import path are complete.
- No trained FreeSOLO router/support deployment IDs are available. The locked Qwen recipes,
  current `flash` CLI adapter, environment packaging, dry-run/quote/confirm lifecycle,
  evaluation gates, import, deploy, export, and gateway endpoint adapters are complete. The seeded
  credential-free demo uses deterministic routing and fake physical endpoints.

No Git repository or remote repository is created by setup.

See [the demo runbook](docs/demo-runbook.md),
[measurement methodology](docs/measurement-methodology.md), and
[implementation matrix](docs/spec-implementation-matrix.md).
