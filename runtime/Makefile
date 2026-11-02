.PHONY: setup test lint demo demo-sim demo-sim-go2 demo-sim-view demo-sim-record demo-yard demo-track schemas bench

setup:
	uv sync --extra dev --extra sim --extra export --extra llm --extra perception

test:
	uv run pytest

lint:
	uv run ruff check spingi tests scripts

demo:
	uv run spingi run plans/demo_inspection_round.yaml

demo-sim:
	uv run spingi run plans/demo_inspection_round.yaml --adapter sim --quiet

demo-sim-go2:
	uv run spingi run plans/demo_inspection_round.yaml --adapter sim --robot go2 --quiet

demo-sim-record:
	uv run spingi run plans/demo_inspection_round.yaml --adapter sim --record --quiet

demo-sim-view:
	uv run mjpython -m spingi.cli run plans/demo_inspection_round.yaml --adapter sim --view --quiet

demo-yard:
	uv run spingi run plans/yard_patrol.yaml --scene sim/scenes/yard_outdoor.yaml --robot go2 --realistic --camera-video --zip --quiet

demo-track:
	uv run spingi run plans/test_track.yaml --scene sim/scenes/test_track.yaml --adapter sim --robot go2 --physics --camera-video --zip --quiet

schemas:
	uv run python scripts/gen_schemas.py

bench:
	uv run spingi bench plans/demo_material_runner.yaml --scene sim/scenes/warehouse_small.yaml --adapter sim --runs 100 --noise 0.2 --sigma 0.02 --gate
