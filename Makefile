# Muse MVP — convenience targets for running golden-path demo scripts.
#
# Prerequisites: docker compose services must be running.
#   docker compose up -d
#
# Usage:
#   make demo-local           # Run the local golden path (no Hub required)
#   make demo-remote          # Run the full path including Hub push/pull/PR
#   make test-golden-path     # Run the pytest integration test

.PHONY: demo-local demo-remote test-golden-path css css-watch help

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

## Compile SCSS → app.css once (requires dart-sass: brew install sass/sass/sass)
css:
	sass agentception/static/scss/app.scss agentception/static/app.css
	@printf '/* !! GENERATED FILE — do not edit directly !!\n   Source: agentception/static/scss/app.scss\n   Compile: make css   (or: sass scss/app.scss app.css)\n   Watch:   make css-watch\n*/\n' | cat - agentception/static/app.css > /tmp/_ac_css && mv /tmp/_ac_css agentception/static/app.css

## Watch SCSS and recompile on save (hot-reload for CSS development)
css-watch:
	sass --watch agentception/static/scss/app.scss:agentception/static/app.css

## Show available targets
help:
	@grep -E '^## ' Makefile | sed 's/## /  /'
	@echo ""
	@echo "Usage: make <target>"
