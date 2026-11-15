.PHONY: setup lint test run local-up local-down cloud-up cloud-down

setup:
	uv sync --all-extras

lint:
	uv run ruff check .
	uv run mypy common ingestion ml serving pipeline

test:
	uv run pytest

run:
	uv run python -m pipeline run --source sample

local-up:
	bash deploy/local-up.sh

local-down:
	bash deploy/local-down.sh

cloud-up:
	bash deploy/cloud-up.sh

cloud-down:
	bash deploy/cloud-down.sh
