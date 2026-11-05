.PHONY: up down logs migrate seed check lint fmt typecheck imports test fe-typecheck openapi api-client bedrock-smoke openai-smoke record e2e external-ingress

up:
	uv run python tools/runtime.py docker compose up -d --build --wait

down:
	docker compose down

logs:
	docker compose logs -f

migrate:
	docker compose exec api python manage.py migrate

seed:
	docker compose exec api python manage.py seed_local
	docker compose exec api python manage.py load_workspace_example \
		seeds/tenants/grupo_sazon/workspace-example.json --tenant grupo_sazon \
		--actor fde@orbio.local --apply

check: lint typecheck imports test fe-typecheck migrations-check

migrations-check:
	uv run python manage.py makemigrations --check --dry-run

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy
	uv run mypy tools

imports:
	uv run lint-imports

test:
	uv run pytest -q

fe-typecheck:
	pnpm -r typecheck

openapi:
	uv run python manage.py export_openapi --output packages/api-client/openapi.json

api-client: openapi
	pnpm --filter @screening/api-client generate

bedrock-smoke:
	docker compose exec api python manage.py bedrock_smoke

openai-smoke:
	uv run python tools/runtime.py uv run python manage.py openai_smoke

record:
	uv run python tools/runtime.py uv run pytest -m live --record-mode=once $(T) -s

external-ingress:
	uv run python tools/runtime.py uv run uvicorn web.external_asgi:application --host 127.0.0.1 --port 8001 --lifespan off --no-access-log --log-level warning

e2e:
	docker compose --env-file /dev/null -p orbio-e2e -f docker-compose.e2e.yml up -d --build --wait
	docker compose --env-file /dev/null -p orbio-e2e -f docker-compose.e2e.yml exec -T api python manage.py seed_local --renew-test-links
	cd tests/e2e && pnpm exec tsc --noEmit -p . && E2E_BASE_URL=http://localhost:15173 SEED_PASSWORD=e2e-only-password pnpm exec playwright test
