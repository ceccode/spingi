# Spingi, the Physical Agent Runtime

Executor of simple physical tasks for humanoid robots, developed and tested in simulation before touching the robot. Specification in [../docs/runtime-spec.md](../docs/runtime-spec.md), decisions in [../adr](../adr/README.md).

All commands are run from this folder. Requires Python 3.11+ and [uv](https://docs.astral.sh/uv/).

```bash
make setup
```

```bash
make test
```

```bash
make demo
```

`make demo` runs the plan [plans/demo_inspection_round.yaml](plans/demo_inspection_round.yaml) on the scene [sim/scenes/lab_small.yaml](sim/scenes/lab_small.yaml) with an in-memory fake robot, prints the event log and saves it to `runs/<run_id>/events.jsonl`. `uv run spingi skills` lists the whitelisted skills with their parameters.

## MuJoCo simulator with the G1

```bash
make demo-sim
```

Same plan with the Unitree G1 model in MuJoCo, as fast as the machine can compute. To watch it in real time (on macOS this goes through `mjpython`, already included):

```bash
make demo-sim-view
```

For a video without a window, in `runs/<run_id>/run.mp4`:

```bash
make demo-sim-record
```

The robot moves kinematically at the requested speed and stops if it hits an obstacle; the legs do not really walk ([ADR-0006](../adr/0006-kinematic-sim-adapter-with-g1-model.md)). The scene [sim/scenes/lab_blocked.yaml](sim/scenes/lab_blocked.yaml) shows the case with a wall on the path: the robot stops, the postcondition fails, the runtime retries and then asks the operator.

## Episodes

Every `spingi run` leaves a complete **episode** in `runs/<run_id>/` ([../docs/episode-format.md](../docs/episode-format.md)): `manifest.json`, `scene.yaml`, `plan.yaml`, `events.jsonl`, `trajectory.jsonl` and, with the simulator, `frames/` and `run.mp4`. With `--zip` it also creates `runs/<run_id>.zip`, ready to share and to load into the viewer. The JSON schemas of the format are regenerated with `make schemas`.

## What is here

- **Core** (`spingi/core`): `WorldState` and `StateDelta`, `Skill` with pure pre and postconditions, declarative `TaskPlan` with `$skill.field` references, `RobotAdapter`, `Perceiver` and `HumanGateway` ports, JSONL event log, `Executor` with deadlines, retries, escalation, e-stop.
- **Adapters**: `FakeAdapter` (in memory) and `SimAdapter` (MuJoCo, G1 from mujoco_menagerie with head camera, collisions, video).
- **Perception**: `FakePerceiver` (configured objects, false negatives) and `SimPerceiver` (ground truth from the MuJoCo scene within range and field of view, optional noise).
- **Safety** (`spingi/safety`): `SafetyMonitor`, an independent task that feeds the watchdog, enforces the geofence and the battery minimum, applies the speed cap, and stops the robot on a violation. Limits come from the `safety:` section of the scene file.
- **Skills**: `navigate`, `inspect` (head-camera frame plus `present:<cls>` / `absent:<cls>` checks through the Perceiver), `say`. Static **Planner** from YAML. YAML scenes.
- **Episode** (`spingi/episode.py`): manifest, trajectory, zip; pydantic models from which the JSON schemas are generated.
- **Tests**: unit, contract tests parametrized over the adapters, MuJoCo scenarios, episode and schemas, architecture test (the core does not import adapters, skills, planner, perception).

## Adding a skill

1. A file in `spingi/skills/`, a class extending `Skill` with `name`, `Params` (pydantic) and the three methods. Preconditions only read the state; `execute` is the only place that talks to the robot.
2. Register it in `default_registry()` in `spingi/skills/__init__.py`.
3. Tests in `tests/unit/` with `FakeAdapter` and `FakePerceiver`: a failing precondition, a `RECOVERABLE` outcome, the success case. If it touches the robot in a new way, a scenario in `tests/sim/`.
4. If it introduces an architectural decision, an ADR.

## Layout

```
spingi/core        types, ports, skills, plan, events, executor, human gateway   (no imports from outside)
spingi/adapters    FakeAdapter, SimAdapter (MuJoCo); UnitreeG1Adapter in M4
spingi/perception  FakePerceiver; markers and detector in M1
spingi/skills      one skill per file
spingi/planner     StaticPlanner; LLMPlanner in M3
plans/          TaskPlan YAML
sim/scenes/     YAML scenes
sim/models/     G1 model (see sim/models/NOTES.md)
tests/          unit · contract · sim · architecture
runs/           run output (ignored by git)
```
