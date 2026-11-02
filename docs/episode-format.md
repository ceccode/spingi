# Episode format v0.1

Status: v0.1 implemented in the runtime and the viewer · Date: 2026-10-02, updated 2026-11-02

An **episode** is the result of a runtime run, packaged so that anyone can replay it without the runtime and without the simulator. It is the contract between the `runtime/` project (which writes it) and the `viewer/` project (which reads it), and it is the form in which episodes are shared and archived as datasets (ADR-0005).

## Principles

- **One folder, simple files.** No proprietary binary formats in the contract: YAML for inputs written by people, JSON and JSONL for outputs written by the runtime, PNG or JPEG for images, MP4 (H.264) for video. A zip of the folder is the unit of exchange.
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
├── trajectory.jsonl     # robot and object poses sampled over time; joint angles when the robot walked in physics
├── terrain.json         # the ground, for scenes that have one (optional)
├── frames/              # camera images, named after the frame_id field of the events (optional)
│   └── sim-1.png        # or sim-1.jpg, from a camera with noise (ADR-0016)
├── head.mp4             # what the head camera saw, continuously, only with `spingi run --camera-video` (optional, ADR-0017)
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
  "config": { "perception_noise": 0.0, "position_sigma_m": 0.0, "seed": 0, "perception": "truth", "locomotion": "kinematic" },
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
| `config` | Optional. The run settings needed to run the episode again with `spingi replay`: `perception_noise` (false-negative rate of the perceiver), `position_sigma_m` (Gaussian noise on perceived positions, metres), `seed`, `perception` (`truth`: the simulator's ground truth; `markers`: AprilTags read from the rendered frames), `locomotion` (`kinematic`: the base glides; `physics`: the robot walks with learned controllers, ADR-0015), `odometry` (`truth`: the robot knows its pose; `drift`: its estimate drifts, ADR-0016) and `camera_noise` (true: blurred, noisy, JPEG-compressed frames). Episodes written before it existed do not have it; replay then uses no noise, seed 0, ground truth and the kinematic base. |
| `camera_video` | Optional, with `spingi run --camera-video`: the head camera recorded continuously (see [head.mp4](#headmp4)). `file` (`head.mp4`), `camera` (the camera's name in the robot model, `head`), `fps` (frames per second of simulated time, 10), `start_t` (simulated time of the first frame), `frames`, `width`, `height`. |
| `files` | Sorted list of the other entries in the folder; directories end with `/`. |

## trajectory.jsonl

One line per sample, at the rate declared in the manifest's `sample_rate_hz` (10 Hz of simulated time with `SimAdapter`, 25 Hz when the robot walks in physics, enough to replay its legs; `FakeAdapter` has no fixed rate, see below). The time `t` is simulated time in seconds since the start of the run, on the same axis as the events.

```json
{"t": 0.0, "robot": {"x": 0.0, "y": 0.0, "yaw": 0.0, "mode": "idle"}}
{"t": 0.1, "robot": {"x": 0.05, "y": 0.0, "yaw": 0.0, "mode": "walking"}}
{"t": 12.2, "robot": {"x": 6.0, "y": 0.5, "yaw": 0.0, "mode": "idle"}, "objects": {"red_box_01": {"x": 0.0, "y": 2.9, "z": 0.9}}}
```

```json
{"t": 3.04, "robot": {"x": 0.81, "y": 0.05, "yaw": 0.02, "mode": "walking", "z": 0.305, "roll": 0.01, "pitch": -0.03}, "battery_pct": 99.6, "joints": {"FL_hip_joint": 0.08, "FL_thigh_joint": 0.91, "FL_calf_joint": -1.62}}
```

Optional fields per line:
- `objects`: object positions; `SimAdapter` writes them in the first sample and then only when they change, for example while an object is carried.
- `robot.z`, `robot.roll`, `robot.pitch`: the base's height in metres and its tilt in radians, when the robot is not on a flat floor at its standing height: always in physics, and in kinematic runs on a scene with ground.
- `joints`: joint angles in radians, by joint name as in the robot's model, written when the robot walks in physics (one value per joint, at most 64).
- `battery_pct`: written by both adapters.
- `estimate`: `{x, y, yaw}`, where the robot believed it was, written while that differs from `robot` (drifting odometry or a landmark correction, ADR-0016). `robot` is always the truth.

## terrain.json

Written when the scene has a `terrain` section (runtime-spec §8.3), so that a reader can draw the ground without the simulator. Heights are on a grid; a reader interpolates between samples.

```json
{"format": 1, "x0": -3.0, "y0": -8.0, "cell_m": 0.1, "rows": 171, "cols": 281,
 "heights_mm": "<base64>", "surface_ids": "<base64>",
 "surfaces": [{"name": "asphalt", "rgb": [0.19, 0.19, 0.21], "speck": [0.34, 0.34, 0.35]}, "..."],
 "obstacles_z": [0.0, 0.1, 0.45]}
```

| Field | Meaning |
|-------|---------|
| `x0`, `y0`, `cell_m`, `rows`, `cols` | The grid: sample `(row, col)` is at x = `x0` + col × `cell_m`, y = `y0` + row × `cell_m`. |
| `heights_mm` | `rows` × `cols` little-endian signed 16-bit integers, row by row: the ground's height in millimetres, built structures (kerbs, ramps, docks) included. |
| `surface_ids` | `rows` × `cols` bytes: the index in `surfaces` of each sample's surface. |
| `surfaces` | Name and colours (base colour and grain, RGB between 0 and 1) of each surface, so the reader needs no table of its own. |
| `obstacles_z` | Where each obstacle of `scene.yaml` stands, in the same order: its base height. |

The viewer refuses grids larger than a million samples, sizes that do not match, colours out of range and surface ids without a surface.

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
| `robot.fell` | `skill`, `reason` (where the robot fell); the run ends as `estop` |
| `robot.relocalized` | `navigate` corrected the robot's pose with a landmark (ADR-0016): `at` (the waypoint, or `here`), `marker_id`, `frame_id`, `frame_ref` (the first of the frames it looked with), `correction_m`, `correction_rad`, `pose` (the corrected pose) |
| `localization.missed` | `at`, `frame_id`, `frame_ref`, `markers`: landmarks that should have been in view and were not seen |
| `localization.rejected` | `at`, `marker_id`, `frame_id`, `jump_m`, `turn_rad`: a reading so far from the estimate that it is taken for a misread |
| `sim.arrival` | written by the simulator, never by the runtime: the end of a walk, with `target`, `truth` and `estimate` (`{x, y, yaw}`), `error_m` (truth against estimate), `miss_m` (truth against target) and `blocked_by` when the walk was stopped. Ground truth for the gate; a real robot has none |
| `run.end` | `status`, `steps_completed`, `reason` |

## Implementation status

- The runtime writes the episode on every `spingi run`: the `runs/<run_id>/` folder is the episode; `--zip` also produces `runs/<run_id>.zip`. `spingi bench` does not write episodes. `spingi replay` reads `plan.yaml`, `scene.yaml`, `events.jsonl`, `trajectory.jsonl` and the manifest's `robot.adapter` and `config` to run an episode again.
- `SimAdapter` samples the trajectory at 10 Hz of simulated time; `FakeAdapter` samples the start and end of every move with an "as if" time computed from the applied walking speed. Both expose `sim_time_s`, and the event log adds `sim_t` to every event.
- The JSON schemas in [schemas/](schemas/) are generated from the pydantic models in `runtime/spingi/episode.py` (`make schemas` in `runtime/`); a test fails if they diverge.
- The first reader is the viewer in `viewer/` (`src/episode.ts` reads zip, YAML and JSONL and interpolates the trajectory). It refuses archives over 200 MB or expanding beyond 500 MB, and trajectories with a missing or non-finite `t`, `x`, `y` or `yaw`.
- `SimAdapter` streams `run.mp4` and `head.mp4` to disk while recording instead of keeping the frames in memory.
- The viewer plays `head.mp4` in an inset over the 3D view ("robot's view"), on the replay's time; it refuses a malformed `camera_video` entry and replays without the video when the archive does not contain it.
- The viewer draws the scene's landmarks as tags on posts or walls, and the `estimate` of each trajectory line as an orange outline tied to the robot by a dashed line, with the gap in the overlay.
- The viewer draws `terrain.json` as a coloured mesh and stands the obstacles on it; it moves the robot's legs from `joints` with the joint table next to each robot model (`models/<robot>.joints.json`: which node each joint turns, about which axis, and the standing angles shown when an episode has no joint angles).
- `spingi export lerobot` refuses folders without a `manifest.json` and trajectories that end outside 0 to 24 hours.

## Frames

A frame is written by the adapter as `frames/<frame_id>.png`, or `frames/<frame_id>.jpg` from a camera with noise (the JPEG the robot sent, ADR-0016), and referenced by the event that produced it: `perception.result` and `robot.relocalized` carry `frame_id` and `frame_ref`, the path relative to the episode folder (never an absolute path of the machine that ran it). A reader places frames on the timeline through those events. The viewer ignores frames that no event references, except when no event references any frame: then it shows all of them at t = 0. `detect` and `inspect` take a frame each; `navigate` takes three at every stop where a landmark should be in view and references the first. No frames are written when the adapter cannot render (headless machines without OpenGL, `FakeAdapter`).

## head.mp4

Written with `spingi run --camera-video` (ADR-0017): the head camera rendered every 5 ticks of the simulation, 10 frames per second of simulated time, at 640 × 480, H.264 in MP4 with a keyframe every 2 s. Frame i was taken at `start_t + i / fps` (`manifest.camera_video`), on the axis of `t` and `sim_t`; frames come at a fixed rate, so a frame that cannot be rendered ends the video instead of leaving a gap. A reader shows, at time t, the last frame taken at or before t, the first frame before it was taken and the last one after the end. The video is the clean render from where the camera really was: the camera noise of `--camera-noise` is in the frames of `frames/`, which the robot analysed, not in the video.

## Compatibility

A viewer that reads `0.1` must tolerate: extra fields in the manifest and in trajectory lines; `objects` missing in lines after the first; `frames/` missing; `sim_t` missing in events (use `ts`); a `status` it does not know (`estop` and `error` were added on 2026-10-04 without a version change); `terrain.json`, `joints` and the base's `z`, `roll`, `pitch` missing (added on 2026-11-01: a flat floor, a standing robot); `estimate` missing in trajectory lines, `landmarks` missing in the scene and frames in JPEG (added on 2026-11-01 too: the robot knew its pose, no tags, a clean camera); `camera_video` and `head.mp4` missing (added on 2026-11-02: no video of the head camera), or `camera_video` present without its file (an archive that left the video out). Episodes written before that date may carry an absolute `frame_ref`; readers should locate frames by `frame_id`. A change that breaks any of these points increments `format_version`.
