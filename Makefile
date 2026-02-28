# Muse MVP — convenience targets for running golden-path demo scripts.
#
# Prerequisites: docker compose services must be running.
#   docker compose up -d
#
# Usage:
#   make demo-local           # Run the local golden path (no Hub required)
#   make demo-remote          # Run the full path including Hub push/pull/PR
#   make test-golden-path     # Run the pytest integration test

.PHONY: demo-local demo-remote test-golden-path help

## Run the local Muse golden path (steps 1–11, no remote required)
demo-local:
	docker compose exec maestro bash /app/scripts/demo_mvp.sh

## Run the full Muse golden path including Hub push/pull/PR (steps 1–15)
## Requires MUSE_HUB_URL to be exported in the shell environment.
demo-remote:
	@if [ -z "$$MUSE_HUB_URL" ]; then \
		echo "❌ MUSE_HUB_URL is not set. Export it before running make demo-remote."; \
		echo "   Example: export MUSE_HUB_URL=https://muse.stori.app"; \
		exit 1; \
	fi
	docker compose exec -e MUSE_HUB_URL -e MUSE_HUB_TOKEN maestro \
		bash /app/scripts/demo_remote.sh

## Run the pytest golden-path integration test inside Docker
test-golden-path:
	docker compose exec maestro \
		pytest tests/e2e/test_muse_golden_path.py -v -s

## Show available targets
help:
	@grep -E '^## ' Makefile | sed 's/## /  /'
	@echo ""
	@echo "Usage: make <target>"
