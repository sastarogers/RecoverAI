.PHONY: up down db-reset install migrate revision api web test lint fmt seed seed-competition verify

up:            ## start postgres
	docker compose up -d db
	@echo "waiting for postgres..." && sleep 3

down:
	docker compose down

db-reset:
	docker compose down -v && docker compose up -d db && sleep 4 && $(MAKE) migrate

install:
	cd backend && python3 -m venv .venv && .venv/bin/pip install -U pip && .venv/bin/pip install -e ".[dev]"

migrate:
	cd backend && .venv/bin/alembic upgrade head

revision:
	cd backend && .venv/bin/alembic revision --autogenerate -m "$(m)"

api:
	cd backend && .venv/bin/uvicorn app.main:app --reload --port 8000

test:
	cd backend && .venv/bin/pytest -q

lint:
	cd backend && .venv/bin/ruff check app tests

fmt:
	cd backend && .venv/bin/ruff format app tests

web:
	cd frontend && npm install --silent && npm run dev

seed:
	cd backend && .venv/bin/python -m scripts.seed --preset demo --reset

seed-competition:
	cd backend && .venv/bin/python -m scripts.seed --preset competition --reset

verify: lint test
	@echo "backend verified"

