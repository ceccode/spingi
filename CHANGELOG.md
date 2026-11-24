# Changelog

What changed for people who use Spingi, newest first. Decisions and their reasons are in the [ADRs](adr/README.md); the milestones are in the [spec](docs/runtime-spec.md#12-milestones).

## 0.2.1 (2026-11-24)

### Added

- `spingi --version` prints the installed version, the first thing a bug report needs.

## 0.2.0 (2026-11-24)

The first version on PyPI: Spingi installs with one line, simulates without the repository, and can be used from a coding agent.

### Added

- **On PyPI**: `uv tool install "spingi[mcp]"` or `pip install "spingi[sim]"`. A release is a tag, published through trusted publishing (ADR-0031).
- The package carries the robot models, the walking policies, the tag images and the sample scenes and plans: an installed `spingi` simulates without the repository (ADR-0027).
- A plan or a scene can be named instead of pointed at: `spingi run demo_inspection_round --adapter sim` runs from any folder. `spingi scenes` and `spingi plans` list the samples, `--show NAME` prints one.

- `spingi check <scene> [--plan P]` says what a robot would meet before anything runs: a place outside the geofence or inside a box, a leg that crosses an obstacle, an object hidden inside one, an object or an inspection with nothing in view, a stop without a landmark (ADR-0028).
- `spingi map <scene>` draws a scene from above as a PNG, with the warnings in red.
- The schema of a scene file is published as `docs/schemas/scene.schema.json`.
- A public Python API, `spingi.api` (ADR-0029): `check_scene`, `check_plan`, `run`, `bench`, `episode_summary` and the rest, by name, with plain data back. It is the stable interface; the rest of the package is internal.

- **An MCP server for coding agents**, `spingi mcp` (ADR-0030): from Claude Code, Codex, OpenCode or any other MCP client an agent models a place as a scene, writes a plan, checks both, runs it in simulation and reads what happened. Seventeen tools, four guides, simulation only. See `docs/agents.md`.

### Changed

- **A scene file with a key nobody declares no longer runs** (ADR-0028). `obstacle:` for `obstacles:`, or `max_sped:` in `safety:`, used to be dropped in silence; now the run ends as `INVALID_SCENE` naming the key. Numbers must be numbers: `true` and `"1"` are refused. A route must name locations of its scene.
- A file that is not a scene ends the command in one line with every problem, instead of a traceback.
- A plan or scene that is neither a file nor a sample ends the command in one line that lists the samples, instead of a traceback.

- The repository says how to get the code, how to contribute and how to report a problem: `CONTRIBUTING.md`, a code of conduct, issue and pull request templates, this changelog.
- The package and the command line describe Spingi as a runtime for legged robots, humanoids and quadrupeds.

- An object's position in a scene must give its height, `z`: it always had to, and the error now says so.

### Fixed

- The skills table of the runtime README lost its last two rows (`wait_for_human`, `say`) to a blank line.
- `make demo-sim-go2`, `make demo-yard`, `make demo-track` and `make schemas` work from the repository root.

## 0.1.0 (2026-10-02 to 2026-11-23, never tagged)

The first public version, used from a clone of the repository.

- **Runtime.** Plans as YAML lists of skills (`navigate`, `detect`, `pick`, `place`, `inspect`, `wait_for_human`, `say`) run on a robot adapter with retries, operator escalation and deadlines; a safety monitor on the robot's clock with geofence, speed cap, battery minimum and watchdog; an e-stop ends the run.
- **Robots.** Profiles with capabilities: the Unitree G1 humanoid and the Unitree Go2 quadruped, which has no arm.
- **Simulation.** A fake adapter for logic and a MuJoCo adapter: kinematic by default, walking with learned controllers with `--physics`, drifting odometry corrected by AprilTag landmarks and a noisy camera with `--realistic`; indoor scenes and outdoor ground with surfaces, ramps and kerbs.
- **Episodes.** Every run leaves an episode (events, trajectory, frames, optionally the head camera's video) that the Spingi Viewer replays in the browser; `spingi replay` runs it again, `spingi export lerobot` turns episodes into a LeRobotDataset v3.0.
- **Measurement.** `spingi bench` runs a plan many times and checks the sim-to-real gate.
- **Planning.** `spingi plan` turns a request in plain language into a validated plan with Claude; `spingi eval-planner` measures it on ten golden requests.
- **Operator console** in the terminal, with Ctrl+C as the stop button.
