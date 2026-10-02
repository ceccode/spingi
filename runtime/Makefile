.PHONY: setup test lint demo demo-sim demo-sim-view demo-sim-record schemas

setup:
	uv sync --extra dev --extra sim

test:
	uv run pytest

lint:
	uv run ruff check spingi tests scripts

demo:
	uv run spingi run plans/demo_inspection_round.yaml

demo-sim:
	uv run spingi run plans/demo_inspection_round.yaml --adapter sim --quiet

demo-sim-record:
	uv run spingi run plans/demo_inspection_round.yaml --adapter sim --record --quiet

demo-sim-view:
	uv run mjpython -m spingi.cli run plans/demo_inspection_round.yaml --adapter sim --view --quiet

schemas:
	uv run python scripts/gen_schemas.py
