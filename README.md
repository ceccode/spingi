# spingi

*Spingi* is Italian for "push!", the word you shout at someone who should keep going. Pronounced *speen-jee*.

Spingi is a **Physical Agent Runtime** for legged robots, humanoids and quadrupeds: it turns a robot into a reliable executor of simple physical tasks (go there, look at this, fetch that, put it here), with safety limits, retries and human escalation built in. It is developed **sim-first**: everything runs and is tested in MuJoCo with the Unitree G1 humanoid and the Unitree Go2 quadruped before any real robot is involved. Every run produces an **episode** that the **Spingi Viewer** replays in the browser.

This repository is a neutral open-source toolkit. Applications built on top of it live elsewhere; this repo keeps sample plans and scenes that show how to use the system.

Status: **runtime M3 complete, two robots in simulation · viewer v0.2** · 2026-10-09

## What is in the box

| Folder | Project | What it does |
|--------|---------|--------------|
| [runtime/](runtime/README.md) | **Spingi runtime** (Python) | Executes a declarative *plan* made of *skills* on a *robot adapter*. Ships with a fake adapter for unit tests and a MuJoCo adapter that drives the Unitree G1 or the Unitree Go2. Writes an episode for every run. |
| [viewer/](viewer/README.md) | **Spingi Viewer** (web, Three.js) | Loads an episode `.zip` and replays it: robot and objects moving in the 3D scene, event timeline, head-camera frames, safety and operator events. Live at **[spingi-viewer.netlify.app](https://spingi-viewer.netlify.app)**. |
| [docs/episode-format.md](docs/episode-format.md) | **Episode format** | The contract between the two: a folder with `manifest.json`, `scene.yaml`, `plan.yaml`, `events.jsonl`, `trajectory.jsonl`, `frames/`. JSON schemas in [docs/schemas/](docs/schemas/). |

## Quick start

Requirements: Python 3.11+ with [uv](https://docs.astral.sh/uv/), Node 20+ for the viewer. No GPU, no robot.

### 1. Run a plan in simulation

```bash
cd runtime && make setup
```

```bash
make demo-sim
```

This runs the inspection round on the G1 in MuJoCo as fast as the CPU allows and leaves an episode in `runtime/runs/<run_id>/`. `make demo-sim-go2` runs the same round on the Go2 quadruped. To watch it live in the MuJoCo viewer (macOS uses `mjpython`, already included):

```bash
make demo-sim-view
```

To fetch a box from a shelf and deliver it to a workstation, routing through the aisle of a small warehouse:

```bash
uv run spingi run plans/demo_material_runner.yaml --scene sim/scenes/warehouse_small.yaml --adapter sim --record --zip
```

`--record` adds a third-person video, `--zip` packs the episode for sharing.

### 2. Replay the episode in the browser

Open **https://spingi-viewer.netlify.app** and drop the `.zip` onto the page, or pick one of the bundled samples. Nothing is uploaded: the episode is read in your browser. To run the viewer locally, from the repository root:

```bash
cd viewer && npm install && npm run dev
```

Then open http://localhost:5173. Space plays and pauses, the arrow keys step one second, clicking an event jumps to it.

### 3. Supervise a run, measure it, export it

From `runtime/` again. Answer the robot's requests yourself instead of a fixed policy, with Ctrl+C as the stop button:

```bash
uv run spingi run plans/demo_material_runner.yaml --scene sim/scenes/warehouse_small.yaml --adapter sim --operator console
```

Run the same plan 100 times with noisy perception and check the sim-to-real gate (success rate, operator requests, fatal runs, safety violations):

```bash
uv run spingi bench plans/demo_material_runner.yaml --scene sim/scenes/warehouse_small.yaml --adapter sim --runs 100 --noise 0.2 --gate
```

Turn episodes into a LeRobotDataset v3.0 for training tools:

```bash
uv run spingi export lerobot runs/r-*/ --out runs/dataset
```

### 4. Ask in plain language

With an Anthropic API key in `runtime/.env` (copy `runtime/.env.example`; the file is ignored by git), Claude turns a request into a plan that is validated against the skills before anything moves, and can run it straight away under your supervision once you confirm it (`--yes` skips the question):

```bash
uv run spingi plan "Bring the red box from shelf A to workstation B" --scene sim/scenes/warehouse_small.yaml --run --adapter sim
```

`spingi eval-planner` measures the planner on ten golden requests. The model never controls the robot: it can only write steps made of whitelisted skills, and a plan that does not validate is never executed.

### 5. Write your own plan

A plan is a YAML list of skills with a failure policy per step. No branches, no loops: when a decision is needed, a new plan is generated.

```yaml
id: fetch_box
description: "Fetch the red box from shelf A and bring it to workstation B"
steps:
  - skill: navigate
    params: { to: shelf_A, via: [aisle_in] }
    on_failure: { retry: 2, then: needs_human }
  - skill: detect
    params: { cls: red_box, expect: 1 }
  - skill: pick
    params: { object_id: "$detect.objects[0].id" }   # reference to the previous step's evidence
  - skill: navigate
    params: { to: workstation_B, via: [aisle_out] }
  - skill: place
    params: { at: workstation_B }
```

Skills available today: `navigate`, `detect`, `pick`, `place`, `inspect`, `wait_for_human`, `say`. `uv run spingi skills` prints their parameters. Each run stands for one robot profile (`--robot g1`, the default, or `--robot go2`): a skill the robot cannot run, `pick` or `place` on a Go2 without an arm, makes the plan invalid before anything moves, and the LLM planner never sees it ([ADR-0012](adr/0012-robot-profiles-and-capabilities.md)). A scene is a YAML file too: named locations, obstacles, objects and safety limits (geofence, speed cap, battery minimum). See [runtime/sim/scenes/](runtime/sim/scenes/).

## How it works

```
request ──► LLMPlanner ──► plan.yaml ──► Executor ──► skills ──► RobotAdapter ──► FakeAdapter | SimAdapter (MuJoCo) | real robot (M4)
                                             │             │
                                             │             └── Perceiver (ground truth in simulation; markers and detector later)
                                             ├── SafetyMonitor: independent task on robot time, geofence (e-stop), speed cap, battery, watchdog
                                             └── EventLog ──► console · metrics · episode (events, trajectory, frames) ──► Viewer, LeRobot
```

Eight principles drive the design, written down in [docs/runtime-spec.md](docs/runtime-spec.md) and in the [ADRs](adr/README.md). The two that matter most: a language model may propose a plan but never controls the robot directly, and every component is testable without hardware.

Safety layers run on the robot's own clock (simulated time in MuJoCo), so a simulation faster than real time is checked as often as the real robot would be. An e-stop ends the run (status `estop`), leaving the geofence e-stops the robot, and any failure inside the runtime stops the robot and still records the episode ([ADR-0011](adr/0011-robot-time-and-terminal-estop.md)).

## Documentation

| Document | Content |
|----------|---------|
| [docs/runtime-spec.md](docs/runtime-spec.md) | Principles, architecture, contracts, execution model, safety layers, testing strategy, milestones |
| [docs/episode-format.md](docs/episode-format.md) | The episode folder, manifest, trajectory, frames, compatibility rules |
| [adr/](adr/README.md) | Architecture decisions 0001–0012 and the ones still open |
| [runtime/README.md](runtime/README.md) | CLI reference, scenes, plans, skills, episodes, tests, how to add a skill or an adapter |
| [viewer/README.md](viewer/README.md) | Running, loading episodes, deploying |
| [SECURITY.md](SECURITY.md) | Threat model and how to report a security or safety problem |

## Development

```bash
make test
```

runs the runtime suite (unit, adapter contract on every adapter and robot, MuJoCo scenarios, golden episodes, architecture and licensing rules): 218 tests in about 14 seconds. The viewer has `npm test` (23 tests) and `npm run build`. CI is GitHub Actions ([.github/workflows/ci.yml](.github/workflows/ci.yml)), on every push to `main` and every pull request, without a GPU: runtime lint, format check and tests (with offscreen MuJoCo rendering through EGL), viewer install, audit of the production dependencies, tests and build.

Conventions: every architectural decision is an ADR, changed by writing a new one; the runtime never imports the viewer and the viewer never imports the runtime, they only share the episode format; `spingi.core` imports nothing from adapters, skills, planner or perception, and a test enforces it.

## Roadmap

- **M2** (done): operator console, `spingi bench` with the sim-to-real gate, LeRobot v3.0 export, `wait_for_human`.
- **M3** (done): LLM planner with structured output, golden episodes replayed on every commit. On the ten golden requests `claude-opus-5-5` scored 10/10, nine at the first attempt.
- **M4** (next, needs a robot): adapter for the real Unitree robots on `unitree_sdk2_python` (the G1 and the Go2 share it, so a Go2 can exercise the hardware path first), the same contract tests on hardware, sim-to-real gates per skill.

## License

Apache License 2.0, see [LICENSE](LICENSE). The Unitree G1 and Go2 models in `runtime/sim/models/`, and the viewer's `g1.glb` and `go2.glb` derived from them, are redistributed under their own BSD 3-Clause license, see [NOTICE](NOTICE); the viewer serves the license text at `/NOTICE.txt`, and the Python package ships copies of `LICENSE` and `NOTICE`.
