# ADR 0001: Local-first adapters and explicit evidence boundaries

Status: accepted

The credential-free stack uses fake model endpoints, fixture carbon data, and a node simulator.
Live Gemini, FreeSOLO, OpenAI-compatible, Carbon Aware, Ollama, vLLM, and hardware paths remain
disabled until explicitly configured. Secrets are referenced only as `env:VARIABLE_NAME` and are
resolved server-side. FreeSOLO is CLI-driven; EcoRoute does not invent a training REST API.

This makes the base-URL compatibility, persistence, safety, caching, routing, quality fallback,
eventing, and reporting paths testable without making unsupported environmental or model-quality
claims.

