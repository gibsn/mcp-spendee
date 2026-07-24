.PHONY: install lint test

install:
	uv sync --all-groups

lint:
	uv run ruff check .
	uv run ruff format --check .

test:
	uv run pytest
