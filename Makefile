# Darwin agent — uv + Hermes submodule + Modal (see README.md).
# Examples: `make setup`  `make test PYTEST_ARGS="tests/test_foo.py"`  `UV=uv`

SHELL := /bin/bash

UV ?= uv
MODAL_APP ?= modal_app.py
PYTEST_ARGS ?=
SMOKE_BACKEND_PORT ?= 8010
SMOKE_MODEL ?= openai/gpt-4o-mini
SMOKE_DARWIN_ID ?= 554c01fe-8db7-4065-bb60-02139d9b9b49

.DEFAULT_GOAL := help

.PHONY: help install submodule sync hermes editable setup test smoke-local deploy clean upgrade-hermes

help: ## Show this help
	@echo "Darwin agent (uv + Modal)"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

submodule: ## Init/update Hermes git submodule (hermes-core)
	git submodule update --init --recursive

sync: ## uv sync with dev extras (pytest stack; see pyproject optional-dependencies)
	$(UV) sync --extra dev

install: sync ## Same as sync (Hermes + plugin: make setup)

hermes: ## Editable install Hermes from submodule (run after submodule)
	$(UV) pip install -e "hermes-core[all]"

editable: ## Editable install darwin_tools (Hermes plugin entry points)
	$(UV) pip install -e .

setup: submodule sync hermes editable ## Full local dev install (ordered prerequisites)

test: ## Run pytest (optional: PYTEST_ARGS="tests/test_finance.py -k foo")
	$(UV) run pytest $(PYTEST_ARGS)

smoke-local: ## Real local runtime smoke: backend up -> check_ledger -> execute_code -> hibernate
	@set -euo pipefail; \
	REPO_ROOT="$$(cd .. && pwd)"; \
	BACKEND_DIR="$$REPO_ROOT/darwin-backend"; \
	echo "==> Starting local backend on 127.0.0.1:$(SMOKE_BACKEND_PORT)"; \
	CORS_ORIGINS='["http://localhost:3000","https://darwinagents.com"]' \
		"$$BACKEND_DIR/.venv/bin/uvicorn" --app-dir "$$BACKEND_DIR" main:app --host 127.0.0.1 --port "$(SMOKE_BACKEND_PORT)" \
		> /tmp/darwin-backend-smoke.log 2>&1 & \
	BACKEND_PID=$$!; \
	trap 'kill $$BACKEND_PID >/dev/null 2>&1 || true' EXIT INT TERM; \
	sleep 2; \
	echo "==> Running Darwin agent smoke flow with model $(SMOKE_MODEL)"; \
	SMOKE_BACKEND_PORT="$(SMOKE_BACKEND_PORT)" SMOKE_MODEL="$(SMOKE_MODEL)" SMOKE_DARWIN_ID="$(SMOKE_DARWIN_ID)" "$(UV)" run python scripts/smoke_local.py; \
	kill $$BACKEND_PID >/dev/null 2>&1 || true; \
	echo "==> Smoke complete"; \
	trap - EXIT INT TERM

deploy: ## Deploy to Modal (modal_app.py; override with MODAL_APP=other.py)
	modal deploy $(MODAL_APP)

clean: ## Remove local caches (__pycache__, pytest; skips .venv/.git)
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . \( -path './.venv' -o -path './.git' \) -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

upgrade-hermes: ## Submodule track remote + sync + reinstall editable Hermes (then run make test)
	git submodule update --remote hermes-core
	$(UV) sync --extra dev
	$(UV) pip install -e "hermes-core[all]"
