# Technical specification implementation matrix

This matrix maps `ECOROUTE_TECHNICAL_SPEC.md` to the local implementation. “Ready” means the code,
validation, persistence, UI boundary, and automated tests exist. It does not claim that an absent
external credential, trained model, proprietary endpoint, or physical host was exercised.

| Area | Status | Implementation evidence |
|---|---|---|
| Workspace and Compose | Ready | Root Compose include without duplicated definitions, pinned Python/Node images, PostgreSQL 17 + pgvector 0.8.5, Redis AOF, Carbon Aware fixture, one-shot migrate/seed, gateway, worker, simulator, two Next apps, optional Prometheus |
| OpenAI-compatible gateway | Ready | `/v1/models`, non-stream and SSE chat completions, official OpenAI SDK contract, system/developer/tool roles, structured JSON Schema, tools, unknown-field forwarding, usage fallback, request IDs and EcoRoute headers |
| Safety and privacy | Ready | Bounded request bodies, bearer auth, typed errors, SSRF/credential-reference validation, redaction, PII/secret/personalization detection, raw prompts off by default, retention minimization, no browser secrets |
| Exact and semantic cache | Ready | Workspace/model/context/schema/tool/namespace fingerprints, pgvector HNSW lookup, top-score + margin policy, strict bypass rules, invalidation preview/confirm, dynamic TTL, cleanup/capacity jobs |
| Router and endpoint selection | Ready | Deterministic fail-closed classifier plus optional FreeSOLO adapter; capability, context, health, region, privacy, latency, cost, quality and task constraints; deterministic tie-break; unknown-carbon safety |
| Provider adapters and fallback | Ready | Fake, OpenAI-compatible/LiteLLM, FreeSOLO, Gemini, Ollama and vLLM registry paths; health reconciliation; timeout/rate-limit/transport fallback; deterministic quality verification and frontier fallback |
| Carbon and impact | Ready | Carbon Aware abstraction, fixture and last-known behavior, Redis cache/background refresh, endpoint-zone attribution, same-token baseline, raw/floored deltas, cache/router overhead, evidence/source/coefficient persistence |
| Durable jobs and events | Ready | PostgreSQL job authority, Redis Streams consumer group, pending recovery, row locks, three-step backoff, periodic health/carbon/cache/retention jobs, capped replayable SSE stream and heartbeats |
| SLM Studio datasets | Ready except live key | Profile/policy versioning, official Gemini structured batches, validation retry, dedupe/safety/split manifest, reviewed manual import, edit/approve/reject, immutable approval/export |
| FreeSOLO lifecycle | Ready except trained artifacts | Current pinned SDK/CLI contracts, `EnvironmentSingleTurn` packages, locked Qwen 2B router and 4B support recipes, SFT→GRPO/OPD gates, dry-run/quote/confirm, poll/cancel/evaluate/deploy/export/import |
| Node agent | Ready | Linux capability detection, NVML/RAPL, cgroup v2, nice/ionice, gateway concurrency, checksum-supervised `sched_ext`, allowlisted NAPI adapter, transactional snapshot/verify/rollback, heartbeat and guardrails, deterministic simulator |
| Benchmarking | Ready | Reproducible prompt/config hash, real-agent assignment and phase protocol, simulator phases, cancellation, persisted metrics/comparison/evidence |
| Control center | Ready | All nine operator sections, global error/loading/empty/evidence states, policy simulator/version activation, full endpoint and logical mapping management, SLM wizard/manual paths, cache, node, audit and filtered reports |
| Northstar support app | Ready | Server-only gateway proxy, fixed logical alias/system prompt, streamed/cancel/retry UX, synthetic personalized orders, no route/model/cache/carbon/debug disclosure to browser |
| Reporting and observability | Ready | Overview/SSE reconciliation, redacted audit timelines, Prometheus metrics, summary/CSV/Impact Framework filters and evidence metadata, UTC-safe formatting, pinned `if-run` manifest validation |
| API lifecycle | Ready | Alembic upgrade/downgrade/check, idempotent seed, cursor pagination (default 50/max 200), generated TypeScript OpenAPI definitions, request correlation |
| Verification | Ready | Unit, provider contract, disposable real-service integration, official OpenAI SDK, migration round trip, Playwright live-stack/responsive/privacy tests, production web builds, expanded demo smoke |

## External handoff items

1. Supply `GEMINI_API_KEY` to execute live dataset generation. No key is fetched or embedded.
2. Supply completed FreeSOLO run/deployment identifiers—or explicitly authorize a quoted training
   run—to replace the deterministic router and fake support SLM endpoints.

Everything else is runnable locally without those artifacts. Real NVML/RAPL/kernel-control evidence
will naturally require an authorized compatible Linux host; the full agent path and simulator are
already implemented.
