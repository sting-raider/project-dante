# Project Dante — task runner (Windows-friendly: use Git Bash / `make` in WSL or run commands directly)
.PHONY: infra up seed api web worker test lint typecheck e2e reset clean

infra:
	docker compose up -d postgres redis

up: infra
	cd apps/api && uv run uvicorn project_dante.api.app:app --reload --port 8000

api:
	cd apps/api && uv run uvicorn project_dante.api.app:app --reload --port 8000

seed:
	cd apps/api && uv run python -m project_dante.db.seed

web:
	cd apps/web && npm run dev

test:
	cd apps/api && uv run pytest -q

lint:
	cd apps/api && uv run ruff check project_dante tests
	cd apps/web && npx next lint || true

typecheck:
	cd apps/api && uv run mypy project_dante --ignore-missing-imports

reset:
	curl -s -X POST http://localhost:8000/api/demo/reset

clean:
	docker compose down -v
