# 0023: Adapters declare what a run leaves for its episode

Status: Accepted · Date: 2026-11-14 · Milestone: M4.0

## Context
An episode (ADR-0005, `docs/episode-format.md`) holds more than the event log: the trajectory, the terrain for the viewer, the head camera's video, the simulated time, the sample rate. The session and the episode writer took these from the adapter by asking whether it had them: `hasattr(adapter, "terrain_for_viewer")`, `getattr(adapter, "trajectory", [])`, `getattr(adapter, "dt", None)`, and `close()` returned the third-person video's path. The `RobotAdapter` port did not mention any of it. An adapter whose method was misnamed, or a new adapter that did not know the convention, produced an episode with no trajectory and no error. The hardware adapter of M4.1 will have to provide the same things, and it can only implement a contract it can read.

## Decision
- **Two methods on the port.** `RobotAdapter` gains `close()`, which ends the run on the adapter's side (viewer, renderers, video files) and can be called twice, and `recording()`, which returns a `Recording`: the trajectory samples, the simulated time, the sample rate, the terrain, the head camera's video and the third-person video. A part an adapter cannot provide is left empty, and the episode then has no such file or field.
- **`close` is a command, `recording` a query.** `close()` returns nothing; the third-person video's path is in the recording.
- **The episode writer reads the recording, not the adapter.** `write_episode` takes a `Recording`, the run's outcome (`RunOutcome`: plan, status, steps) and the robot (`RobotInfo`) instead of the adapter and twelve separate arguments.
- **Settings checked once, when they are made.** `SessionConfig` refuses settings that contradict each other (markers, physics, drift, camera noise or videos without the simulator; drift without physics) when it is built, and `RunConfig`, the settings an episode keeps, has the same names and values: an episode with a value the runtime does not know is refused when it is read, and `spingi replay` passes it to a session as it is.

## Consequences
- **Same episodes.** The episode format does not change; the manifest schema now lists the values `perception`, `locomotion` and `odometry` can take. The golden replays and the whole runtime suite pass, with new tests for the CLI, the contract and the retries of `navigate`.
- **The contract test covers it.** Every adapter, fake and simulated, on every robot, is checked to close twice and to return a recording of the walk it made. The hardware adapter will be held to the same.
- **Earlier errors.** A contradicting setting fails before the scene or the robot model is loaded, and the CLI says it in one line instead of a traceback.
- **Supersedes part of 0020.** `camera_video_for_viewer` and `terrain_for_viewer`, kept by ADR-0020, are replaced by `recording()`.
