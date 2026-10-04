# Spingi runtime

The Physical Agent Runtime: a Python package (`spingi`) that executes declarative plans on a robot adapter, with safety limits, retries and human escalation, and records every run as an episode. Specification in [../docs/runtime-spec.md](../docs/runtime-spec.md), decisions in [../adr](../adr/README.md).

All commands run from this folder. Requirements: Python 3.11+ and [uv](https://docs.astral.sh/uv/).

## Install

```bash
make setup
```

installs the package with its extras: `dev` (pytest, ruff), `sim` (MuJoCo, numpy, imageio, and trimesh with scipy for the viewer model export), `export` (pandas, pyarrow) and `llm` (the Anthropic SDK, used only by `spingi plan` and `spingi eval-planner`). Nothing else is needed: the G1 model is in `sim/models/`.

## Command line

```
spingi run <plan.yaml> [--scene S] [--adapter fake|sim] [--operator auto|console] [--on-failure abort|skip|retry]
           [--noise P] [--sigma M] [--seed K] [--runs-dir D] [--view] [--record] [--realtime] [--zip] [--quiet]
spingi bench <plan.yaml> [--scene S] [--adapter fake|sim] [--runs N] [--noise P] [--sigma M] [--seed K]
             [--on-failure abort|skip|retry] [--runs-dir D] [--gate] [--keep-failed]
spingi export lerobot <episode_dir>... --out <dataset_dir> [--fps 10]
spingi plan "<request>" [--scene S] [--out plan.yaml] [--model M] [--run [--adapter fake|sim] [--operator auto|console]]
spingi eval-planner [--cases plans/golden/planner_cases.yaml] [--model M] [--out report.json] [--min-pass-rate R]
spingi replay <episode_dir>...
spingi skills
```

| Option | Effect |
|--------|--------|
| `--adapter fake` (default) | In-memory robot: instantaneous moves, no physics. For logic and unit tests. |
| `--adapter sim` | Unitree G1 in MuJoCo: realistic travel times, collisions with obstacles, head camera, kinematic grasp. |
| `--scene` | Scene file; default `sim/scenes/lab_small.yaml`. |
| `--operator auto` (default) | Requests for a human are answered with `--on-failure` (abort, skip or retry) and the event log is printed at the end. |
| `--operator console` | You supervise: live event lines on the terminal, prompts when a step needs you, Ctrl+C to e-stop the robot and end the run. |
| `--on-failure` | The fixed answer of `--operator auto` (default `abort`); in `spingi bench`, the answer to every operator request. |
| `--noise`, `--sigma`, `--seed` | Perception noise: false-negative rate (0..1), position noise in metres, random seed (default 0; `bench` uses seed, seed+1, ...). |
| `--runs-dir` | Where episodes and bench reports go; default `runs/`. |
| `--quiet` | Prints only the final summary. |
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
| `make bench` | Material runner in `warehouse_small`, 100 simulated runs with 20 % perception false negatives and 0.02 m position noise, gate checked |
| `make test`, `make lint` | Test suite, ruff |
| `make schemas` | Regenerates the episode JSON schemas in `../docs/schemas/` from the pydantic models |

Sample plans and scenes:

| Plan | Scene | Level | What it shows |
|------|-------|-------|---------------|
| `plans/demo_inspection_round.yaml` | `sim/scenes/lab_small.yaml` | L1 | Four waypoints, camera checks at two of them (one finds the box, one does not), safety monitor armed |
| `plans/demo_material_runner.yaml` | `sim/scenes/warehouse_small.yaml` | L2 | Aisle waypoints, detect, pick, carry, place on a table, return |
| any `navigate` to `workstation_B`, e.g. `tests/golden/plans/to_workstation.yaml` | `sim/scenes/lab_blocked.yaml` | – | A wall across the path: the robot stops, retries, asks the operator |
| any `navigate` to `workstation_B` | `sim/scenes/lab_geofence.yaml` | – | A target outside the geofence: the safety monitor stops the robot |

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

In simulation the same file generates the MuJoCo scene (floor, racks, box, cameras) and feeds the ground-truth perceiver. Routes are not planned automatically: when a straight line crosses an obstacle, the plan lists waypoints (`via`). An optional `routes:` section (`"dock->shelf_A": [aisle_in]`) tells the LLM planner which waypoints to use; see `sim/scenes/warehouse_small.yaml`. Perception noise is not part of the scene: it is a run option (`--noise`, `--sigma`, `--seed`).

## Plans

A plan is an ordered list of steps. Each step names a skill, its parameters and what to do on failure: how many times to retry, then `needs_human`, `abort` or `skip`. A parameter can reference the evidence of a previous step with `$<skill or index>.<field>` as its whole value, for example `"$detect.objects[0].id"`. Plans are validated before anything moves: unknown skills, invalid parameters and forward references are rejected. A step can override its skill's deadline with `deadline_s`, and a plan can set a total `deadline_s` for the run, checked before each step.

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

Every `spingi run` leaves an episode in `runs/<run_id>/` ([../docs/episode-format.md](../docs/episode-format.md)): `manifest.json`, copies of the plan and the scene, `events.jsonl` (every event with wall-clock and simulated time), `trajectory.jsonl` (robot pose at 10 Hz, object positions when they change), `frames/` (head-camera PNGs referenced by `perception.result` events, written only when an OpenGL context is available) and, with `--record`, `run.mp4`. `manifest.json` also records the perception noise and seed, so that `spingi replay` can run the episode again.

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

At 60 % false negatives the same plan drops to about 74 % success with 26 operator requests per 100 runs, and the gate fails: the retries in the plan absorb moderate noise, not heavy noise.

## Export to LeRobot

`spingi export lerobot` turns episodes into a LeRobotDataset v3.0 folder (ADR-0009): `meta/info.json`, `meta/stats.json`, `meta/tasks.parquet`, `meta/episodes/…parquet`, `data/…parquet`. Each frame, at 10 Hz, has `observation.state` and `action` as base x, y, yaw and gripper state (the action is the next state), the plan description as the task, and `next.done` / `next.success` from the outcome. Camera images are not exported yet: Spingi frames are taken per inspection, not at a fixed rate. Checked against lerobot 0.6.1: the exported folder loads with `LeRobotDataset(repo_id, root=...)`. Requires the `export` extra (pandas, pyarrow), installed by `make setup`.

## Planning from natural language

`spingi plan` sends the request to Claude (`claude-opus-5-5` by default, `--model` to change it) together with the skill summaries and the symbolic world of the scene: locations, known objects, battery, and the `routes:` section of the scene file, which tells the model which waypoints avoid the shelving. The answer is constrained by a JSON schema generated from the skill registry, so it can only contain whitelisted skills with parameters of the right shape; then it goes through the same validation as a hand-written plan. An invalid plan is sent back once with the errors; a second failure, a refusal or a truncated answer is reported and nothing runs (ADR-0010).

The API key goes in `runtime/.env`, which git ignores:

```bash
cp .env.example .env    # then set ANTHROPIC_API_KEY=sk-ant-... in .env
```

Spingi reads `.env` from the current folder, or else from `runtime/`, at startup; a variable already exported in the shell wins over the file. Never put the key in `.env.example`, which is committed.

```bash
uv run spingi plan "Bring the red box to workstation B, then wait for the operator" --scene sim/scenes/warehouse_small.yaml --out plans/my_delivery.yaml
uv run spingi plan "Check that the red box is on shelf A" --scene sim/scenes/warehouse_small.yaml --run --adapter sim
```

`--run` executes the plan right away, by default on the fake adapter with the operator console (`--adapter sim` for MuJoCo); without `--out` the plan is saved to `runs/plan-<id>.yaml`. For recurring tasks keep a static plan in `plans/`: deterministic and free.

**Evaluation.** `plans/golden/planner_cases.yaml` holds ten requests with the expected plan: deliveries, an inspection round, a route through the aisle, a speed limit, a confirmation step, and two requests that cannot be done (the right answer is a single `say` explaining why). Two plans are equivalent when they use the same skills in the same order and the same parameters, with defaults filled in; announcement texts, confirmation prompts and failure policies are not compared, and a reference by step name equals the same reference by index. `spingi eval-planner` runs the ten requests and prints the pass rate; `--min-pass-rate` sets the exit code. Each run makes about ten API calls. Last result (2026-10-04, `claude-opus-5-5`): 10/10, nine at the first attempt; for the unknown blue crate the first plan failed validation and the corrected one explained that no such object is known. A test checks offline that every golden plan is valid and runs in simulation.

## Replay and golden episodes

`spingi replay <episode>` runs a recorded episode again with the plan, scene, adapter, perception noise, seed and operator answers it recorded, then compares the behaviour: steps, skill outcomes, retries, operator requests and answers, safety events, final status, final position within 5 cm. Timestamps are not compared.

`tests/golden/episodes/` holds three recorded runs: the material runner with 50 % perception noise and two retries, the inspection round, and the wall that blocks the robot with an operator who answers retry, then abort. They are replayed on every commit; a difference means the runtime, a skill or the simulator changed behaviour. After an intended change, re-record them with `uv run python scripts/record_golden.py` and review the diff.

## Architecture in one screen

```
spingi/core        types, ports (RobotAdapter, Perceiver, HumanGateway), Skill, TaskPlan, EventLog, Executor, ScriptedHuman
spingi/skills      one file per skill
spingi/safety      SafetyMonitor and limits
spingi/perception  FakePerceiver, SimPerceiver (ground truth from the scene)
spingi/adapters    FakeAdapter, sim_mujoco/ (G1 in MuJoCo)
spingi/planner     StaticPlanner (YAML), LLMPlanner (Claude, structured output), schema, evaluation
spingi/console     terminal operator console (reporter + HumanGateway)
spingi/export      LeRobot v3.0 exporter
spingi/session.py  one run: adapter, perceiver, safety monitor, executor, episode (shared by run and bench)
spingi/metrics.py  metrics from the event log, the sim-to-real gate
spingi/bench.py    many runs with noise, report
spingi/replay.py   run an episode again and compare behaviour
spingi/episode.py  episode writer and the pydantic models behind docs/schemas
spingi/scenes.py   initial WorldState, safety limits and routes from a scene file
spingi/cli.py      the `spingi` command; spingi/dotenv.py loads .env
plans/  sim/scenes/  sim/models/  tests/  scripts/ (gen_schemas, record_golden, export_g1_glb)
```

`spingi.core` imports nothing from the other packages; `tests/test_architecture.py` enforces it.

## Tests

```bash
make test
```

138 tests, about 11 seconds. Unit tests on the fake adapter, contract tests run against every adapter (fake and sim), scenario tests in MuJoCo (inspection round, warehouse delivery, wall, geofence, speed cap, rendering, Ctrl+C operator stop, every golden plan), golden episodes replayed, metrics and gate, bench, console, LeRobot export, the LLM planner with a fake client (request shape, schema, retry with errors, refusals), episode and schema tests, architecture rules. No test calls the network. Rendering tests skip themselves on machines without an OpenGL context.

## Extending

**A skill**: a file in `spingi/skills/`, a class extending `Skill` with `name`, `Params` (pydantic) and `preconditions` / `execute` / `postconditions`; register it in `default_registry()`; unit tests with `FakeAdapter` and `FakePerceiver`, a scenario test if it moves the robot in a new way; an ADR if it introduces a design decision.

**An adapter**: implement the `RobotAdapter` protocol in `spingi/adapters/`, add it to `ADAPTERS` in `tests/contract/test_adapter_contract.py` so the shared contract suite runs on it. Adapters translate; they hold no task logic.

**A perceiver**: implement `detect` and `localize` from the `Perceiver` protocol.

**An operator interface** (web console, chat, pager): implement `HumanGateway.ask`, honouring `request.options`, and subscribe to the `EventLog` to show the run live.
