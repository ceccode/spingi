# Spingi runtime

The Physical Agent Runtime: a Python package (`spingi`) that executes declarative plans on a robot adapter, with safety limits, retries and human escalation, and records every run as an episode. Specification in [../docs/runtime-spec.md](../docs/runtime-spec.md), decisions in [../adr](../adr/README.md).

All commands run from this folder. Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/).

## Install

```bash
make setup
```

installs the package with the `dev` and `sim` extras (pytest, ruff, MuJoCo, imageio). Nothing else is needed: the G1 model is in `sim/models/`.

## Command line

```
spingi run <plan.yaml> [--scene S] [--adapter fake|sim] [--operator auto|console] [--view] [--record] [--realtime] [--zip]
spingi bench <plan.yaml> [--scene S] [--adapter fake|sim] [--runs N] [--noise P] [--sigma M] [--seed K] [--gate]
spingi export lerobot <episode_dir>... --out <dataset_dir> [--fps 10]
spingi skills
```

| Option | Effect |
|--------|--------|
| `--adapter fake` (default) | In-memory robot: instantaneous moves, no physics. For logic and unit tests. |
| `--adapter sim` | Unitree G1 in MuJoCo: realistic travel times, collisions with obstacles, head camera, kinematic grasp. |
| `--scene` | Scene file; default `sim/scenes/lab_small.yaml`. |
| `--operator auto` (default) | Requests for a human are answered with `--on-failure` (abort, skip or retry) and the event log is printed at the end. |
| `--operator console` | You supervise: live event lines on the terminal, prompts when a step needs you, Ctrl+C to e-stop the robot and end the run. |
| `--noise`, `--sigma` | Perception noise: false-negative rate (0..1) and position noise in metres. |
| `--view` | Opens the MuJoCo viewer in real time. On macOS run through `mjpython`: `uv run mjpython -m spingi.cli run … --view`. |
| `--record` | Saves a third-person video to `runs/<run_id>/run.mp4`. |
| `--realtime` | Simulates at wall-clock speed instead of as fast as possible. |
| `--zip` | Also writes `runs/<run_id>.zip`, the episode ready to share or to load in the viewer. |

Make targets wrap the common cases:

| Target | Command |
|--------|---------|
| `make demo` | Inspection round on the fake adapter |
| `make demo-sim` | Same plan on the G1 in MuJoCo |
| `make demo-sim-view` | Same, in the MuJoCo viewer |
| `make demo-sim-record` | Same, with video |
| `make bench` | Material runner, 100 simulated runs with 20 % perception false negatives, gate checked |
| `make test`, `make lint` | Test suite, ruff |
| `make schemas` | Regenerates the episode JSON schemas in `../docs/schemas/` from the pydantic models |

Sample plans and scenes:

| Plan | Scene | Level | What it shows |
|------|-------|-------|---------------|
| `plans/demo_inspection_round.yaml` | `sim/scenes/lab_small.yaml` | L1 | Four waypoints, camera checks at two of them (one finds the box, one does not), safety monitor armed |
| `plans/demo_material_runner.yaml` | `sim/scenes/warehouse_small.yaml` | L2 | Aisle waypoints, detect, pick, carry, place on a table, return |
| any `navigate` | `sim/scenes/lab_blocked.yaml` | – | A wall across the path: the robot stops, retries, asks the operator |
| any `navigate` | `sim/scenes/lab_geofence.yaml` | – | A target outside the geofence: the safety monitor stops the robot |

## Scenes

A scene is the world the robot knows: named locations with a pose and a tolerance, obstacles, objects, safety limits.

```yaml
robot:
  pose: { x: 0.0, y: 0.0, yaw: 0.0 }
locations:
  dock:    { pose: { x: 0.0, y: 0.0, yaw: 0.0 } }
  shelf_A: { pose: { x: 4.0, y: 3.0, yaw: 0.0 }, tolerance_m: 0.15 }
obstacles:
  - { x: 5.1, y: 3.0, w: 0.5, d: 4.0, h: 1.6 }   # a rack: collides in simulation
objects:
  red_box_01: { cls: red_box, marker_id: 7, pose: { x: 4.75, y: 3.0, z: 0.95 } }
safety:
  geofence: { x_min: -1.0, x_max: 10.0, y_min: -1.0, y_max: 9.0 }
  max_speed: 0.8
  min_battery_pct: 5
```

In simulation the same file generates the MuJoCo scene (floor, racks, box, cameras) and feeds the ground-truth perceiver. Routes are not planned automatically: when a straight line crosses an obstacle, the plan lists waypoints (`via`).

## Plans

A plan is an ordered list of steps. Each step names a skill, its parameters and what to do on failure: how many times to retry, then `needs_human`, `abort` or `skip`. A parameter can reference the evidence of a previous step with `$<skill or index>.<field>` as its whole value, for example `"$detect.objects[0].id"`. Plans are validated before anything moves: unknown skills, invalid parameters and forward references are rejected.

## Skills

| Skill | Parameters | Precondition | Effect |
|-------|------------|--------------|--------|
| `navigate` | `to`, `via=[]`, `max_speed=0.5` | known locations, battery above minimum | robot within tolerance of `to` |
| `detect` | `cls`, `expect=1` | robot idle | objects of class `cls` added to the world state with their pose |
| `pick` | `object_id`, `arm=right` | object pose known and within 0.9 m, hand empty | `robot.holding` set, object removed from the scene |
| `place` | `at`, `arm=right` | holding something, robot at `at` | object put down ahead of the robot, `holding` cleared |
| `inspect` | `target`, `checks=[]` | robot at `target` | a head-camera frame; `present:<cls>` / `absent:<cls>` checks evaluated, anomalies listed |
| `wait_for_human` | `prompt`, `timeout_s=300` | – | the robot stops and waits; the operator answers `continue` or `abort` |
| `say` | `text` | – | the text in the event log |

Skills never call each other; composition is the plan's job. Pre and postconditions are pure functions of the world state, which makes them unit-testable without a robot.

## Episodes

Every `spingi run` leaves an episode in `runs/<run_id>/` ([../docs/episode-format.md](../docs/episode-format.md)): `manifest.json`, copies of the plan and the scene, `events.jsonl` (every event with wall-clock and simulated time), `trajectory.jsonl` (robot pose at 10 Hz, object positions when they change), `frames/` (head-camera PNGs referenced by `perception.result` events) and, with `--record`, `run.mp4`.

## Operator console

`--operator console` turns the terminal into the supervision console (ADR-0008):

```
Operator console: Ctrl+C stops the robot.
[    0.0s]    speed cap 0.8 m/s
[    0.0s] run r-20261004-011212-64b3bf · plan demo_material_runner · 8 steps
[    0.0s] step 0  say {"text": "Fetching the red box"}
[    0.0s]    says "Fetching the red box"
[    0.0s] step 1  navigate {"to": "shelf_A", "via": ["aisle_in"]}
[   17.2s] step 2  detect {"cls": "red_box", "expect": 1}
[   17.2s]    looking for red_box: nothing
[   17.2s]    detect -> recoverable: found 0 'red_box', expected 1
[   17.2s]    retry 1
…
  OPERATOR NEEDED · step 2 (detect)
  found 0 'red_box', expected 1
  [r]etry  [s]kip  [a]bort > a
[   17.2s]    operator: abort
[   17.2s] run end: aborted · 2 steps completed
```

Only the answers the request offers are accepted: retry, skip or abort for a failed step; continue or abort for `wait_for_human`. Ctrl+C emits `operator.stop`, e-stops the robot, ends the run as aborted and still writes the episode. It does not replace the hardware e-stop.

## Benchmarks and the sim-to-real gate

`spingi bench` runs the same plan many times, with a different perception seed each time, and computes the metrics of the spec (section 8.5) from the event logs alone: success rate, operator requests per 100 runs, retries, fatal runs, safety violations, simulated time p50 and p95. It checks them against the gate of section 8.4 (success ≥ 95 %, operator requests ≤ 5 per 100, no fatal run, no safety violation); with `--gate` a failed gate exits with status 1, ready for CI. The report goes to `runs/bench-<id>/report.json`, one line per run to `runs.jsonl`, and with `--keep-failed` the event log of every failed run is kept.

```
plan demo_material_runner · scene sim/scenes/warehouse_small.yaml · adapter sim · 100 runs
perception noise: false negatives 20%, position sigma 0.02 m

  ok    success rate        100.0%   (gate >= 95%)
  ok    operator requests   0.0 per 100 runs   (gate <= 5)
  ok    fatal runs          0   (gate <= 0)
  ok    safety violations   0   (gate <= 0)
        retries per run     0.25
        simulated time       p50 72.2 s · p95 72.2 s

GATE PASSED
```

At 60 % false negatives the same plan drops to about 72 % success and the gate fails: the retries in the plan absorb moderate noise, not heavy noise.

## Export to LeRobot

`spingi export lerobot` turns episodes into a LeRobotDataset v3.0 folder (ADR-0009): `meta/info.json`, `meta/stats.json`, `meta/tasks.parquet`, `meta/episodes/…parquet`, `data/…parquet`. Each frame, at 10 Hz, has `observation.state` and `action` as base x, y, yaw and gripper state (the action is the next state), the plan description as the task, and `next.done` / `next.success` from the outcome. Camera images are not exported yet: Spingi frames are taken per inspection, not at a fixed rate. Checked against lerobot 0.6.1: the exported folder loads with `LeRobotDataset(repo_id, root=...)`. Requires the `export` extra (pandas, pyarrow), installed by `make setup`.

## Architecture in one screen

```
spingi/core        types, ports (RobotAdapter, Perceiver, HumanGateway), Skill, TaskPlan, EventLog, Executor
spingi/skills      one file per skill
spingi/safety      SafetyMonitor and limits
spingi/perception  FakePerceiver, SimPerceiver (ground truth from the scene)
spingi/adapters    FakeAdapter, sim_mujoco/ (G1 in MuJoCo)
spingi/planner     StaticPlanner (YAML); LLMPlanner planned for M3
spingi/console     terminal operator console (reporter + HumanGateway)
spingi/export      LeRobot v3.0 exporter
spingi/session.py  one run: adapter, perceiver, safety monitor, executor, episode (shared by run and bench)
spingi/metrics.py  metrics from the event log, the sim-to-real gate
spingi/bench.py    many runs with noise, report
spingi/episode.py  episode writer and the pydantic models behind docs/schemas
plans/  sim/scenes/  sim/models/  tests/  scripts/
```

`spingi.core` imports nothing from the other packages; `tests/test_architecture.py` enforces it.

## Tests

```bash
make test
```

Unit tests on the fake adapter, contract tests run against every adapter, scenario tests in MuJoCo (inspection round, warehouse delivery, wall, geofence, speed cap, rendering, Ctrl+C operator stop), metrics and gate, bench, console, LeRobot export, episode and schema tests, architecture rules. Rendering tests skip themselves on machines without an OpenGL context.

## Extending

**A skill**: a file in `spingi/skills/`, a class extending `Skill` with `name`, `Params` (pydantic) and `preconditions` / `execute` / `postconditions`; register it in `default_registry()`; unit tests with `FakeAdapter` and `FakePerceiver`, a scenario test if it moves the robot in a new way; an ADR if it introduces a design decision.

**An adapter**: implement the `RobotAdapter` protocol in `spingi/adapters/`, add it to `ADAPTERS` in `tests/contract/test_adapter_contract.py` so the shared contract suite runs on it. Adapters translate; they hold no task logic.

**A perceiver**: implement `detect` and `localize` from the `Perceiver` protocol.

**An operator interface** (web console, chat, pager): implement `HumanGateway.ask`, honouring `request.options`, and subscribe to the `EventLog` to show the run live.
