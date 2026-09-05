.PHONY: dev test lint migrate benchmark seed replay-demo

dev:
	docker compose up --build

test:
	pytest

lint:
	ruff check .
	mypy backend apps/api apps/worker

migrate:
	alembic upgrade head

benchmark:
	python -m evaluation.run_benchmark --seed 20260901 --customers 10000

seed:
	python -m scripts.seed_demo

replay-demo:
	docker compose exec api python -m scripts.replay_demo_webhooks --mode recovery
