# EcoRoute demo and operations runbook

The portable demo uses deterministic fake model endpoints, fixture grid data, and a simulated
host. Every corresponding impact or telemetry value is labeled `simulated`; it is not an
environmental claim.

## Start and verify

```bash
cp .env.example .env
docker compose up --build -d --wait
./scripts/demo-smoke
```

The one-shot `migrate` and `seed` services must exit successfully. Inspect the stack with:

```bash
docker compose ps -a
curl --fail http://localhost:8000/readyz
```

Open http://localhost:3000 and http://localhost:3001 in separate windows. The customer-facing
Northstar page never receives the gateway key or EcoRoute response headers.

## Presentation acceptance flow

1. In **Overview**, select the clean fixture. Ask Northstar “What is your return window for unused
   items?” Open the live audit event.
2. Ask the exact question again, then “How many days do I have to send back something unused?”
   The audit shows exact and conservative semantic reuse only for the same context hash.
3. Select the dirty fixture and ask a new public-policy question. The approved specialized fake
   endpoint is preferred.
4. Ask the legal comparison prompt from `infra/fixtures/prompts.yaml`. It bypasses both caches and
   small/specialized routes and selects a frontier endpoint.
5. Select **Force next quality failure**, then ask another public-policy question. Audit shows the
   failed specialized attempt and successful frontier fallback.
6. In **Self-Hosted Nodes**, change the simulator profile and inspect snapshot/apply/verify events.
   High-risk confirmations are one-shot and appear only for server-approved controls.
7. Start the reproducible simulator benchmark and inspect baseline/optimized comparisons. The demo
   uses 30-second phases; real-agent defaults follow the 60/180/60-second protocol.
8. In **Impact Reports**, filter the same requests and export redacted CSV and Impact Framework
   YAML. Both exports include methodology, filters, generation time, and evidence counts.

`./scripts/demo-smoke` automates gateway readiness, exact/semantic cache headers, dirty-grid
specialized routing, high-risk frontier routing, forced quality fallback, both report exports,
simulator registration, and both web application entry pages. It resets demo controls to the clean
fixture on exit, including after a failed assertion, so repeated runs are deterministic.

After `make bootstrap`, validate that the export is executable with the pinned Green Software
Foundation Impact Framework CLI:

```bash
make impact-validate
```

This fetches a fresh manifest and runs `if-run --no-output` from `@grnsft/if==1.1.1`. EcoRoute's
empty pipeline deliberately preserves its precomputed, evidence-labeled observations.

## Enable Gemini later

Set only the key supplied by the project owner; EcoRoute never obtains one itself:

```dotenv
GEMINI_API_KEY=your-key
GEMINI_DATASET_MODEL=gemini-2.5-flash
```

Restart `worker` and `gateway`. Dataset generation uses the official `google-genai` async client,
JSON structured output, batches of at most 50, a single validation retry, secret/identifier
rejection, near-duplicate detection, paraphrase-group split isolation, and a mandatory review and
approval gate. Use **Import reviewed examples** in SLM Studio when a key is unavailable.

## Connect FreeSOLO later

The pinned authoring/runtime boundary is `freesolo-flash==1.0.0` and `freesolo==0.2.56`. Set:

```dotenv
FREESOLO_API_KEY=your-key
# FREESOLO_ORG is optional compatibility metadata.
```

The worker invokes shell-free allowlisted arrays corresponding to:

```text
flash env push --name NAME DIRECTORY
flash train CONFIG --dry-run
flash train CONFIG --cost
flash train CONFIG --background
flash status RUN_ID
flash log RUN_ID
flash cancel RUN_ID
flash deploy RUN_ID --dry-run
flash deploy RUN_ID
flash export --adapter-id RUN_ID --repository REPOSITORY
```

No command runs merely because a credential exists. A launch requires an approved immutable
dataset, environment validation, successful dry-run, a current quote, matching quote ID, and an
explicit confirmation. Alternatively import a completed run/deployment through SLM Studio or
`POST /api/v1/training-runs/import`; deployment registration still enforces evaluation gates and
isolates explicitly experimental models.

## Real Linux node agent

Run `python -m ecoroute_agent.real_main` only on an authorized Linux inference host. Capabilities
are detected before controls are exposed. Server and local allowlists must both approve a control.
The agent snapshots state to a mode-0600 file, applies in risk order, verifies, rolls back on any
failure or guardrail breach, protects against PID reuse, and restores after heartbeat loss. GPU
power limits, `sched_ext`, and NAPI controls remain disabled without their separate explicit
confirmations and configuration.

## Stop or reset

```bash
docker compose down
# Destructive local demo reset only:
docker compose down --volumes
```
