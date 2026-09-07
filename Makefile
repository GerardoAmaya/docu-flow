.PHONY: up down logs migrate revision shell psql test

up:
	docker compose up -d --build

down:
	docker compose down

logs:
	docker compose logs -f api worker

migrate:
	docker compose exec api alembic upgrade head

revision:
	docker compose exec api alembic revision --autogenerate -m "$(m)"

shell:
	docker compose exec api bash

psql:
	docker compose exec db psql -U docuflow -d docuflow
