PYTHON ?= $(if $(wildcard .venv/bin/python),.venv/bin/python,python3)
COMPOSE ?= docker compose

.PHONY: bootstrap lint format-check format typecheck test-unit test-integration test-contract test-e2e test-all db-upgrade db-check seed-demo demo-smoke impact-validate compose-config generate-api

bootstrap:
	./scripts/bootstrap

lint:
	$(PYTHON) -m ruff check services training
	pnpm lint

format-check:
	$(PYTHON) -m ruff format --check services training

format:
	$(PYTHON) -m ruff format services training

typecheck:
	$(PYTHON) -m mypy services/gateway/ecoroute services/worker/ecoroute_worker services/node-agent/ecoroute_agent
	pnpm typecheck

test-unit:
	$(PYTHON) -m pytest -q services/gateway/tests/unit services/worker/tests services/node-agent/tests

test-integration:
	PYTHON=$(PYTHON) ./scripts/test-integration

test-contract:
	$(PYTHON) -m pytest -q services/gateway/tests/contract

test-e2e:
	PYTHON=$(PYTHON) ./scripts/test-e2e

test-all: lint format-check typecheck test-unit test-integration test-contract test-e2e

db-upgrade:
	$(COMPOSE) exec gateway alembic -c services/gateway/alembic.ini upgrade head

db-check:
	$(COMPOSE) exec gateway alembic -c services/gateway/alembic.ini check

seed-demo:
	$(COMPOSE) exec gateway python -m ecoroute.seed

demo-smoke:
	./scripts/demo-smoke

impact-validate:
	./scripts/validate-impact

compose-config:
	$(COMPOSE) config

generate-api:
	pnpm generate:api
