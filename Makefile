.PHONY: help install run docker-build docker-run docker-up docker-down init-db init-db-all db-check alembic-init alembic-migrate alembic-upgrade alembic-revision

help:
	@echo "Usage:"
	@echo "  make install           Install dependencies locally"
	@echo "  make run               Run locally"
	@echo "  make docker-build      Build Docker image"
	@echo "  make docker-run        Build and run in Docker"
	@echo "  make docker-up         Start in Docker (background)"
	@echo "  make docker-down       Stop Docker container"
	@echo "  make init-db           Create missing MSSQL tables"
	@echo "  make init-db-all       Initialize all databases"
	@echo "  make db-check          Check database connections"
	@echo "  make alembic-init      Initialize alembic"
	@echo "  make alembic-migrate   Create initial migration"
	@echo "  make alembic-upgrade   Apply migrations"
	@echo "  make alembic-revision  Create new migration"

install:
	uv sync

run:
	uvicorn app.main:app --reload --host 0.0.0.0 --port 8000

docker-build:
	docker build -t semanticsearchengine .

docker-run: docker-build
	docker run --rm --env-file docker.env --network host -p 8000:8000 semanticsearchengine

docker-up:
	docker compose up -d

docker-down:
	docker compose down

init-db:
	python scripts/init_db.py --db mssql

init-db-all:
	python scripts/init_db.py --db all

db-check:
	python scripts/init_db.py --check-only

alembic-init:
	alembic init alembic

alembic-migrate:
	alembic revision --autogenerate -m "initial"

alembic-upgrade:
	alembic upgrade head

alembic-downgrade:
	alembic downgrade -1

alembic-revision:
	alembic revision --autogenerate -m "$(MSG)"
