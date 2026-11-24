.PHONY: install dev worker lint format typecheck test test-integration check migrate sync-cards revision up down

install:
	uv sync

dev:
	uv run uvicorn app.main:app --reload --port 8000

worker:
	uv run arq app.workers.settings.WorkerSettings

lint:
	uv run ruff check .
	uv run ruff format --check .

format:
	uv run ruff format .
	uv run ruff check --fix .

typecheck:
	uv run mypy app

test:
	uv run pytest

# Needs a Postgres, e.g. `docker compose up -d postgres` then create a pulse_test database.
test-integration:
	TEST_DATABASE_URL=$${TEST_DATABASE_URL:-postgresql+asyncpg://postgres:postgres@localhost:5432/pulse_test} uv run pytest

check: lint typecheck test

migrate:
	uv run alembic upgrade head

sync-cards:
	uv run python -m app.services.card_catalog

revision:
	uv run alembic revision --autogenerate -m "$(m)"

up:
	docker compose up --build

down:
	docker compose down
