# Project Dante — task runner (Windows-friendly: use Git Bash / `make` in WSL or run commands directly)
.PHONY: infra up seed api web setup test lint typecheck e2e reset clean

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

setup:
	cd apps/api && uv sync --extra dev && cd ../../apps/web && npm install

test: setup
	cd apps/api && uv run pytest -q

lint: setup
	cd apps/api && uv run ruff check project_dante tests
	cd apps/web && npm run lint
	cd apps/web && npx tsc --noEmit

typecheck: setup
	cd apps/api && uv run mypy project_dante --ignore-missing-imports

e2e:
	cd apps/api && .venv/Scripts/python.exe ../../scripts/verify_e2e.py

reset:
	curl -s -X POST http://localhost:8000/api/demo/reset

clean:
	docker compose down -v
