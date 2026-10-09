# Episode format v0.1

Status: v0.1 implemented in the runtime and the viewer · Date: 2026-10-02, updated 2026-10-04

An **episode** is the result of a runtime run, packaged so that anyone can replay it without the runtime and without the simulator. It is the contract between the `runtime/` project (which writes it) and the `viewer/` project (which reads it), and it is the form in which episodes are shared and archived as datasets (ADR-0005).

## Principles

- **One folder, simple files.** No proprietary binary formats in the contract: YAML for inputs written by people, JSON and JSONL for outputs written by the runtime, PNG for images. A zip of the folder is the unit of exchange.
- **The viewer recomputes nothing.** Everything needed to draw is written in the episode: robot pose over time, object positions, events. No physics in the browser.
- **Versioned.** `manifest.json` declares `format_version`; a viewer rejects versions it does not know with a clear message.
- **Extended by the runtime, not by the viewer.** The runtime may add fields; the viewer ignores the ones it does not know.

## Structure

```
<run_id>/
├── manifest.json        # format version, run_id, robot, plan, outcome, durations, run settings, file list
├── scene.yaml           # copy of the scene used (locations, obstacles, objects, safety limits)
├── plan.yaml            # copy of the executed plan
├── events.jsonl         # runtime event log, unchanged (ADR-0005)
├── trajectory.jsonl     # robot and object poses sampled over time
├── frames/              # camera images, named after the frame_id field of the events (optional)
│   └── sim-1.png
└── run.mp4              # third-person video, only with `spingi run --record` (optional; the viewer ignores it)
```

The zip produced by `--zip` (`runs/<run_id>.zip`) contains the `<run_id>/` folder itself, not only its content.

## manifest.json

```json
{
  "format_version": "0.1",
  "run_id": "r-20261004-022645-bc34e6",
  "created_at": "2026-10-04T00:26:45Z",
  "robot": { "model": "unitree_g1", "adapter": "sim_mujoco" },
  "plan_id": "demo_inspection_round",
  "status": "success",
  "steps_completed": 9,
  "steps_total": 9,
  "duration_s": 0.504,
  "sim_time_s": 39.36,
  "sample_rate_hz": 10.0,
  "config": { "perception_noise": 0.0, "position_sigma_m": 0.0, "seed": 0 },
  "files": ["events.jsonl", "frames/", "plan.yaml", "scene.yaml", "trajectory.jsonl"]
}
```

| Field | Meaning |
|-------|---------|
| `format_version` | Always `"0.1"` for this version. |
| `run_id`, `created_at` | Run identifier; creation time, RFC 3339 in UTC. |
| `robot` | `model`: the robot profile the run stood for (`unitree_g1`, `unitree_go2`; see `runtime/spingi/robots.py`), which the viewer uses to pick the 3D model; `adapter`: what executed it (`fake`, `sim_mujoco`; the real robots' adapters later). Episodes recorded before robot profiles existed say `fake` as the model: they ran as the G1. |
| `plan_id`, `steps_total`, `steps_completed` | The executed plan and how far it got. |
| `status` | `success`, `aborted`, `invalid_plan`, `deadline`, `estop` (the robot was e-stopped: geofence, a `FATAL` skill outcome, a safety monitor failure) or `error` (the run raised; the robot was stopped), as in the `run.end` event. |
| `duration_s` | Wall-clock seconds between the first and the last event. In fast simulation it is much shorter than `sim_time_s`. |
| `sim_time_s` | Total simulated time, when the adapter has a simulated clock (optional). |
| `sample_rate_hz` | Nominal rate of `trajectory.jsonl`, when the adapter samples at a fixed rate (optional). |
| `config` | Optional. The run settings needed to run the episode again with `spingi replay`: `perception_noise` (false-negative rate of the perceiver), `position_sigma_m` (Gaussian noise on perceived positions, metres), `seed`. Episodes written before it existed do not have it; replay then uses no noise and seed 0. |
| `files` | Sorted list of the other entries in the folder; directories end with `/`. |

## trajectory.jsonl

One line per sample, at the rate declared in the manifest's `sample_rate_hz` (10 Hz of simulated time with `SimAdapter`; `FakeAdapter` has no fixed rate, see below). The time `t` is simulated time in seconds since the start of the run, on the same axis as the events.

```json
{"t": 0.0, "robot": {"x": 0.0, "y": 0.0, "yaw": 0.0, "mode": "idle"}}
{"t": 0.1, "robot": {"x": 0.05, "y": 0.0, "yaw": 0.0, "mode": "walking"}}
{"t": 12.2, "robot": {"x": 6.0, "y": 0.5, "yaw": 0.0, "mode": "idle"}, "objects": {"red_box_01": {"x": 0.0, "y": 2.9, "z": 0.9}}}
```

Optional fields per line: `objects` (object positions; `SimAdapter` writes them in the first sample and then only when they change, for example while an object is carried), `joints` (joint positions, once locomotion is real, not written yet), `battery_pct` (written by both adapters today).

## Aligning events and trajectory

Runtime events carry `ts` in wall-clock time. To align them with the trajectory, the runtime adds a `sim_t` field to every event when the adapter provides it. The viewer uses `sim_t` if present, otherwise `ts - ts[run.start]`.

## Events

`events.jsonl` is the runtime event log, one JSON object per line with `ts`, `run_id`, `kind`, `sim_t` when available, and fields that depend on the kind (event kinds in [runtime-spec.md](runtime-spec.md), section 3.8). The fields a reader is most likely to use:

| Kind | Fields |
|------|--------|
| `step.start` | `index`, `skill`, `params` (references already resolved) |
| `skill.end` | `skill`, `attempt`, `outcome` (`success`, `recoverable`, `needs_human`, `fatal`), `reason`, `duration_s` |
| `perception.result` from `detect` | `frame_id`, `frame_ref` (`frames/<frame_id>.png`, relative to the episode folder, or `null` when no image was written), `cls`, `found` (list of object ids) |
| `perception.result` from `inspect` | `frame_id`, `frame_ref`, `target`, `checks` (check → `{passed, ...}`, `passed` is `null` for a check that was not evaluated), `anomalies` (checks that failed) |
| `human.request` | `skill`, `reason`; `index` for a failed step, whose implicit options are `retry`, `skip`, `abort`; `options` (`["continue", "abort"]`) for `wait_for_human` |
| `human.response` | `action` (`retry`, `skip`, `abort` or `continue`), `note`; `index` or `skill` as in the request |
| `human.timeout` | the operator did not answer in time |
| `operator.stop` | `reason`: the operator pressed Ctrl+C, the robot was e-stopped and the run ends as `aborted` |
| `safety.geofence`, `safety.battery_low` | the Safety Monitor stopped the robot (leaving the geofence e-stops it unless the scene sets `estop_on_geofence: false`); `pose` or `battery_pct`, `min_pct`. Recorded once per occurrence |
| `safety.speed_capped` | `limit` (the scene's `max_speed`), `applied` (the cap now in force on the adapter) |
| `safety.estop`, `safety.monitor_error` | the robot was e-stopped after a `FATAL` outcome (`skill`, `reason`) or after a safety check raised (`error`) |
| `run.end` | `status`, `steps_completed`, `reason` |

## Implementation status

- The runtime writes the episode on every `spingi run`: the `runs/<run_id>/` folder is the episode; `--zip` also produces `runs/<run_id>.zip`. `spingi bench` does not write episodes. `spingi replay` reads `plan.yaml`, `scene.yaml`, `events.jsonl`, `trajectory.jsonl` and the manifest's `robot.adapter` and `config` to run an episode again.
- `SimAdapter` samples the trajectory at 10 Hz of simulated time; `FakeAdapter` samples the start and end of every move with an "as if" time computed from the applied walking speed. Both expose `sim_time_s`, and the event log adds `sim_t` to every event.
- The JSON schemas in [schemas/](schemas/) are generated from the pydantic models in `runtime/spingi/episode.py` (`make schemas` in `runtime/`); a test fails if they diverge.
- The first reader is the viewer in `viewer/` (`src/episode.ts` reads zip, YAML and JSONL and interpolates the trajectory). It refuses archives over 200 MB or expanding beyond 500 MB, and trajectories with a missing or non-finite `t`, `x`, `y` or `yaw`.
- `SimAdapter` streams `run.mp4` to disk while recording instead of keeping the frames in memory.
- `spingi export lerobot` refuses folders without a `manifest.json` and trajectories that end outside 0 to 24 hours.

## Frames

A frame is written by the adapter as `frames/<frame_id>.png` and referenced by the event that produced it: `perception.result` carries `frame_id` and `frame_ref`, the path relative to the episode folder (`frames/<frame_id>.png`; never an absolute path of the machine that ran it). A reader places frames on the timeline through those events. The viewer ignores frames that no event references, except when no event references any frame: then it shows all of them at t = 0. Only `detect` and `inspect` take frames, so most episodes have few of them, and none when the adapter cannot render (headless machines without OpenGL, `FakeAdapter`).

## Compatibility

A viewer that reads `0.1` must tolerate: extra fields in the manifest and in trajectory lines; `objects` missing in lines after the first; `frames/` missing; `sim_t` missing in events (use `ts`); a `status` it does not know (`estop` and `error` were added on 2026-10-04 without a version change). Episodes written before that date may carry an absolute `frame_ref`; readers should locate frames by `frame_id`. A change that breaks any of these points increments `format_version`.
