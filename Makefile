# Darwin agent — uv + Hermes submodule + Modal (see README.md).
# Examples: `make setup`  `make test PYTEST_ARGS="tests/test_foo.py"`  `UV=uv`

UV ?= uv
MODAL_APP ?= modal_app.py
PYTEST_ARGS ?=

.DEFAULT_GOAL := help

.PHONY: help submodule sync hermes editable setup test deploy clean upgrade-hermes

help: ## Show this help
	@echo "Darwin agent (uv + Modal)"
	@echo ""
	@grep -E '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  %-18s %s\n", $$1, $$2}'

submodule: ## Init/update Hermes git submodule (hermes-core)
	git submodule update --init --recursive

sync: ## uv sync with dev extras (pytest stack; see pyproject optional-dependencies)
	$(UV) sync --extra dev

hermes: ## Editable install Hermes from submodule (run after submodule)
	$(UV) pip install -e "hermes-core[all]"

editable: ## Editable install darwin_tools (Hermes plugin entry points)
	$(UV) pip install -e .

setup: submodule sync hermes editable ## Full local dev install (ordered prerequisites)

test: ## Run pytest (optional: PYTEST_ARGS="tests/test_finance.py -k foo")
	$(UV) run pytest $(PYTEST_ARGS)

deploy: ## Deploy to Modal (modal_app.py; override with MODAL_APP=other.py)
	modal deploy $(MODAL_APP)

clean: ## Remove local caches (__pycache__, pytest; skips .venv/.git)
	rm -rf .pytest_cache .ruff_cache .mypy_cache
	find . \( -path './.venv' -o -path './.git' \) -prune -o -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

upgrade-hermes: ## Submodule track remote + sync + reinstall editable Hermes (then run make test)
	git submodule update --remote hermes-core
	$(UV) sync --extra dev
	$(UV) pip install -e "hermes-core[all]"
