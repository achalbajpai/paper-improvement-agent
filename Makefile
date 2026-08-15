


COMPOSE := docker compose
API     := $(COMPOSE) exec -T api
API_RUN := $(COMPOSE) run --rm -T api

.DEFAULT_GOAL := help
.PHONY: help up down logs ready test test-slow web-prod web-dev live-smoke \
        lint typecheck fmt client shell db clean

help:
	@printf '  \033[1m%-13s\033[0m %s\n' help 'List targets'
	@printf '  \033[1m%-13s\033[0m %s\n' up 'Build and start postgres, grobid, api, web'
	@printf '  \033[1m%-13s\033[0m %s\n' down 'Stop everything'
	@printf '  \033[1m%-13s\033[0m %s\n' clean 'Stop everything and drop volumes (recreates the database)'
	@printf '  \033[1m%-13s\033[0m %s\n' logs 'Follow api logs'
	@printf '  \033[1m%-13s\033[0m %s\n' ready 'Wait for grobid and the api to answer'
	@printf '  \033[1m%-13s\033[0m %s\n' test 'Fast suites, both sides (no network, no live providers)'
	@printf '  \033[1m%-13s\033[0m %s\n' test-slow 'Real GROBID and the acceptance end-to-end test'
	@printf '  \033[1m%-13s\033[0m %s\n' web-prod 'Serve a production web build (for screenshots)'
	@printf '  \033[1m%-13s\033[0m %s\n' web-dev 'Restore the development web server'
	@printf '  \033[1m%-13s\033[0m %s\n' live-smoke 'Real LLM + real providers. Never part of make test.'
	@printf '  \033[1m%-13s\033[0m %s\n' lint 'ruff + eslint'
	@printf '  \033[1m%-13s\033[0m %s\n' typecheck 'mypy --strict on domain/ + services/, tsc on web/'
	@printf '  \033[1m%-13s\033[0m %s\n' fmt 'Format both sides'
	@printf '  \033[1m%-13s\033[0m %s\n' client 'Regenerate the typed API client from the FastAPI OpenAPI schema'
	@printf '  \033[1m%-13s\033[0m %s\n' shell 'Shell inside the api container'
	@printf '  \033[1m%-13s\033[0m %s\n' db 'psql inside the postgres container'

up:
	$(COMPOSE) up --build -d
	@$(MAKE) --no-print-directory ready

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down -v

logs:
	$(COMPOSE) logs -f api

ready:
	@printf 'waiting for grobid '
	@until curl -fsS http://localhost:8070/api/health >/dev/null 2>&1; do printf '.'; sleep 3; done; echo ' ok'
	@printf 'waiting for api    '
	@until curl -fsS http://localhost:8000/ready >/dev/null 2>&1; do printf '.'; sleep 2; done; echo ' ok'
	@echo 'web  http://localhost:3000'

test:
	$(API) pytest -m "not slow" -q
	cd web && pnpm test

test-slow:
	$(API) pytest -m slow -q

web-prod:
	$(COMPOSE) run --rm -T --no-deps web pnpm build
	@docker rm -f atw-web-prod >/dev/null 2>&1 || true
	$(COMPOSE) stop web
	$(COMPOSE) run --rm -d --no-deps --service-ports --name atw-web-prod web pnpm start
	@printf 'waiting for the production server '
	@until curl -fsS http://localhost:3000 >/dev/null 2>&1; do printf '.'; sleep 2; done; echo ' ok'
	@echo 'production build on http://localhost:3000 -- run `make web-dev` when finished'

web-dev:
	@docker rm -f atw-web-prod >/dev/null 2>&1 || true
	$(COMPOSE) up -d --no-deps web

live-smoke:
	$(API) python -m app.scripts.live_smoke

lint:
	$(API_RUN) ruff check app tests
	$(API_RUN) ruff format --check app tests
	cd web && pnpm lint

fmt:
	$(API_RUN) ruff format app tests
	$(API_RUN) ruff check --fix app tests
	cd web && pnpm exec prettier --write .

typecheck:
	$(API) mypy app
	cd web && pnpm exec tsc --noEmit

client:
	curl -fsS http://localhost:8000/openapi.json -o web/lib/api/openapi.json
	cd web && pnpm exec openapi-typescript lib/api/openapi.json -o lib/api/schema.d.ts

shell:
	$(COMPOSE) exec api bash

db:
	$(COMPOSE) exec postgres psql -U answerthis -d answerthis
