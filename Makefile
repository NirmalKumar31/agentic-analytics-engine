# Clone and run: `make bootstrap && make data && make dev`.
#
# Every target here works with no API credentials, and pins that explicitly.
#
# The default provider is a deterministic scripted stand-in, so tests, the
# demo dataset, the recorded runs and the evaluation all run offline and for
# free. "Default", though, is not the same as "guaranteed": settings load
# `.env`, so a repository that has one can select a paid provider for any
# command that does not say otherwise. Ordinary development should not be one
# keystroke away from spending money.
#
# So every target below that executes application code sets $(FAKE)
# explicitly. An environment variable beats `.env`, which makes the posture a
# property of the command rather than of whatever happens to be on disk.
#
# A direct CLI invocation still loads `.env` and may therefore select another
# provider -- that flexibility is deliberate and is documented in the README.
# `make dev-cloud` is the supported way to ask for it, and it refuses to start
# without an unmistakable confirmation.

PY      := .venv/bin/python
PIP     := .venv/bin/python -m pip
RUFF    := .venv/bin/ruff
MYPY    := .venv/bin/mypy
PYTEST  := .venv/bin/python -m pytest
WEB     := web
PORT    ?= 8000

# Pins the provider for every target that runs application code. Explicit
# environment beats `.env`, so this holds whatever a local `.env` says.
FAKE    := AAE_PROVIDER_MODE=fake

.DEFAULT_GOAL := help
.PHONY: help bootstrap data test test-cov lint format typecheck frontend frontend-test \
        e2e e2e-install live-acceptance capacity-smoke resource-rehearsal \
        dev dev-cloud confirm-paid-local serve record verify evaluate docker docker-run \
        clean constraints wheel audit all

help: ## Show the available targets
	@grep -hE '^[a-zA-Z0-9_-]+:.*?## ' $(MAKEFILE_LIST) \
	 | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-16s\033[0m %s\n", $$1, $$2}'

bootstrap: ## Create the virtualenv, install Python and frontend dependencies
	python3.12 -m venv .venv || python3 -m venv .venv
	$(PIP) install --upgrade pip
	$(PIP) install -c constraints-dev.txt -e ".[dev]"
	cd $(WEB) && npm ci --no-audit --no-fund
	@echo "\nReady. Next: make data && make dev"

data: ## Generate the deterministic demo warehouse
	$(FAKE) $(PY) -m agentic_analytics.cli generate-data

test: ## Run the Python test suite
	$(FAKE) $(PYTEST)

test-cov: ## Run the tests with branch coverage
	$(FAKE) $(PYTEST) --cov --cov-branch --cov-report=term-missing --cov-report=xml

lint: ## Lint and check formatting
	$(RUFF) check .
	$(RUFF) format --check .

format: ## Apply formatting and safe lint fixes
	$(RUFF) check --fix .
	$(RUFF) format .

typecheck: ## Run mypy over the package and the release scripts
	$(MYPY)
	# `mypy` alone checks the package. The scripts are release-critical --
	# they are what decides whether a deployment is accepted -- so they are
	# checked too rather than being the one untyped corner.
	$(MYPY) scripts

frontend: ## Build the production frontend bundle
	cd $(WEB) && npm run build

frontend-test: ## Typecheck and test the frontend
	cd $(WEB) && npm run typecheck && npm run test

e2e-install: ## Download the browsers Playwright drives
	cd $(WEB) && npx playwright install --with-deps chromium

e2e: ## Browser tests against a server you are already running (AAE_E2E_BASE_URL)
	cd $(WEB) && AAE_E2E_BASE_URL=$${AAE_E2E_BASE_URL:-http://127.0.0.1:$(PORT)} npx playwright test

live-acceptance: ## Acceptance checks against a deployed URL: make live-acceptance URL=https://...
	$(PY) scripts/live_acceptance.py $(URL)

capacity-smoke: ## Small bounded concurrency check. Not a throughput benchmark.
	$(PY) scripts/capacity_smoke.py $(URL)

resource-rehearsal: ## Fill the configured session envelope and watch memory. Not a benchmark.
	$(PY) scripts/resource_rehearsal.py $(URL) $(if $(CONTAINER),--container $(CONTAINER)) $(if $(PID),--pid $(PID))

dev: data frontend ## Generate data, build the frontend, serve locally. No credentials, no cost.
	$(FAKE) AAE_LIVE_ANALYTICS_ENABLED=true AAE_LOG_JSON=false \
	  $(PY) -m agentic_analytics.cli serve --port $(PORT)

# Ordered first among `dev-cloud`'s prerequisites so the refusal is immediate,
# rather than after a data generation and a frontend build.
confirm-paid-local:
	@if [ "$$AAE_CONFIRM_PAID_LOCAL_RUN" != "1" ]; then \
	  echo "refusing to start a paid local run."; \
	  echo ""; \
	  echo "  make dev-cloud sends real requests to a paid provider and bills"; \
	  echo "  the account whose credentials the process loads. Ordinary UI and"; \
	  echo "  browser work does not need it: make dev runs the same graph, the"; \
	  echo "  same MCP tools and the same DuckDB against a scripted provider."; \
	  echo ""; \
	  echo "  If you mean it:"; \
	  echo ""; \
	  echo "    AAE_CONFIRM_PAID_LOCAL_RUN=1 make dev-cloud"; \
	  echo ""; \
	  exit 1; \
	fi

dev-cloud: confirm-paid-local data frontend ## Serve with the cloud provider. Spends money. Needs AAE_CONFIRM_PAID_LOCAL_RUN=1
	AAE_PROVIDER_MODE=cloud AAE_LIVE_ANALYTICS_ENABLED=true AAE_LOG_JSON=false \
	  $(PY) -m agentic_analytics.cli serve --port $(PORT)

serve: ## Serve in recorded-only mode, the safe public posture
	$(FAKE) $(PY) -m agentic_analytics.cli serve --port $(PORT)

record: data ## Re-record the demo runs (fails if one does not pass acceptance)
	$(FAKE) $(PY) -m agentic_analytics.cli record

verify: lint typecheck test frontend-test ## Everything CI runs, locally
	$(FAKE) $(PY) -m agentic_analytics.cli validate-recordings
	@echo "\nAll checks passed."

evaluate: data ## Score the agents against the injected ground truth
	$(FAKE) $(PY) -m agentic_analytics.cli evaluate --out var/evaluation/report.json

wheel: ## Build the distribution wheel
	$(PIP) install --quiet build
	$(PY) -m build --wheel

constraints: ## Regenerate the pinned runtime closure
	$(PY) scripts/freeze_constraints.py

audit: ## Check dependencies for known vulnerabilities
	# Audits the pinned runtime closure, which is what the image installs.
	# Auditing the editable working tree instead would flag the local
	# package as "not on PyPI" and audit dev-only tools that never ship.
	$(PY) -m pip_audit --strict --progress-spinner off -r constraints.txt
	cd $(WEB) && npm audit --omit=dev

docker: ## Build the production image
	docker build -t agentic-analytics-engine:local .

docker-run: ## Run the production image on $(PORT)
	docker run --rm -p $(PORT):8000 -e PORT=8000 agentic-analytics-engine:local

clean: ## Remove build output and caches
	rm -rf dist build .pytest_cache .mypy_cache .ruff_cache htmlcov .coverage coverage.xml
	rm -rf $(WEB)/dist $(WEB)/node_modules/.tmp
	find . -name '__pycache__' -type d -prune -exec rm -rf {} +

all: bootstrap data verify frontend record evaluate ## Full local build
