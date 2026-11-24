# Changelog

What changed for people who use Spingi, newest first. Decisions and their reasons are in the [ADRs](adr/README.md); the milestones are in the [spec](docs/runtime-spec.md#12-milestones).

## Unreleased

### Changed

- The repository says how to get the code, how to contribute and how to report a problem: `CONTRIBUTING.md`, a code of conduct, issue and pull request templates, this changelog.
- The package and the command line describe Spingi as a runtime for legged robots, humanoids and quadrupeds.

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
