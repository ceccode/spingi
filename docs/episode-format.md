# Episode format: draft v0

Status: v0.1 implemented in the runtime · Date: 2026-10-02

An **episode** is the result of a runtime run, packaged so that anyone can replay it without the runtime and without the simulator. It is the contract between the `runtime/` project (which writes it) and the `viewer/` project (which reads it), and it is the form in which episodes are shared and archived as datasets (ADR-0005).

## Principles

- **One folder, simple files.** No proprietary binary formats in the contract: YAML for inputs written by people, JSON and JSONL for outputs written by the runtime, PNG for images. A zip of the folder is the unit of exchange.
- **The viewer recomputes nothing.** Everything needed to draw is written in the episode: robot pose over time, object positions, events. No physics in the browser.
- **Versioned.** `manifest.json` declares `format_version`; a viewer rejects versions it does not know with a clear message.
- **Extended by the runtime, not by the viewer.** The runtime may add fields; the viewer ignores the ones it does not know.

## Structure

```
episode-<run_id>/
├── manifest.json        # format version, run_id, robot, duration, outcome, file list
├── scene.yaml           # copy of the scene used (locations, obstacles, objects)
├── plan.yaml            # copy of the executed plan
├── events.jsonl         # runtime event log, unchanged (ADR-0005)
├── trajectory.jsonl     # robot and object poses sampled over time
└── frames/              # camera images, named after the frame_id field of the events
    └── sim-1.png
```

## manifest.json

```json
{
  "format_version": "0.1",
  "run_id": "r-20261002-005553-756a",
  "created_at": "2026-10-02T00:55:53Z",
  "robot": { "model": "unitree_g1", "adapter": "sim_mujoco" },
  "status": "success",
  "duration_s": 39.4,
  "sim_time_s": 39.4,
  "files": ["scene.yaml", "plan.yaml", "events.jsonl", "trajectory.jsonl", "frames/"]
}
```

## trajectory.jsonl

One line per sample, at a fixed rate declared in the manifest (default 10 Hz of simulated time). The time `t` is simulated time in seconds since the start of the run, on the same axis as the events.

```json
{"t": 0.0, "robot": {"x": 0.0, "y": 0.0, "yaw": 0.0, "mode": "idle"}}
{"t": 0.1, "robot": {"x": 0.05, "y": 0.0, "yaw": 0.0, "mode": "walking"}}
{"t": 12.2, "robot": {"x": 6.0, "y": 0.5, "yaw": 0.0, "mode": "idle"}, "objects": {"red_box_01": {"x": 0.0, "y": 2.9, "z": 0.9}}}
```

Optional fields per line: `objects` (only when they change), `joints` (joint positions, once locomotion is real), `battery_pct`.

## Aligning events and trajectory

Runtime events carry `ts` in wall-clock time. To align them with the trajectory, the runtime adds a `sim_t` field to every event when the adapter provides it. The viewer uses `sim_t` if present, otherwise `ts - ts[run.start]`.

## Implementation status

- The runtime writes the episode on every `spingi run`: the `runs/<run_id>/` folder is the episode; `--zip` also produces `runs/<run_id>.zip`.
- `SimAdapter` samples the trajectory at 10 Hz of simulated time; `FakeAdapter` samples the start and end of every move with an "as if" time computed from the applied walking speed. Both expose `sim_time_s`, and the event log adds `sim_t` to every event.
- The JSON schemas in [schemas/](schemas/) are generated from the pydantic models in `runtime/spingi/episode.py` (`make schemas`); a test fails if they diverge.
- The first reader is the viewer in `viewer/` (`src/episode.ts` reads zip, YAML and JSONL and interpolates the trajectory).

## Frames

A frame is written by the adapter as `frames/<frame_id>.png` and referenced by the event that produced it: `perception.result` carries `frame_id` (and `frame_ref`, the path the adapter wrote). A reader places frames on the timeline through those events; a frame without a referencing event is shown at t = 0.

## Compatibility

A viewer that reads `0.1` must tolerate: extra fields in the manifest and in trajectory lines; `objects` missing in lines after the first; `frames/` missing; `sim_t` missing in events (use `ts`). A change that breaks any of these points increments `format_version`.
