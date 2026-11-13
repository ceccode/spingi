# 0020: SimAdapter delegates contacts, objects, frames and videos

Status: Accepted · Date: 2026-11-13 · Milestone: M4.0

## Context
`SimAdapter` was one class of about 800 lines. Besides walking the robot it read MuJoCo's contacts, carried and put down objects, rendered the head camera's frames with their noise and camera model, and wrote two videos. A change to any of these meant reading all of it, and none could be tested or reused alone. The hardware adapter of M4.1 will need the same split: it will record videos and take frames from a real camera, but never read MuJoCo contacts. The simulator must stay exactly repeatable: the golden episodes and the bench depend on the same seed giving the same run.

## Decision
- **Four parts outside the adapter.** `contacts.py` has functions of the model and its state: the robot's geoms, the ground's, what the robot touches and what it pushes on. `objects.py` has `SceneObjects`, the scene's objects and the one in the hand (ADR-0007). `rendering.py` has `Renderers` (one MuJoCo renderer per image size), `HeadCamera` (the frames the runtime takes, their noise, their camera model) and `Videos` (`run.mp4` and `head.mp4`).
- **The adapter keeps what is the robot.** Pose, mode, battery, e-stop, watchdog, odometry, the walking loops and the trajectory samples stay in `SimAdapter`, which owns the parts and passes them what they need.
- **The outside is unchanged.** The constructor's parameters, the `RobotAdapter` port, and what sessions and tests read (`held_object`, `render_error`, `image`, `object_positions`, `camera_video_for_viewer`) keep their names and meaning. The random streams are created in the same order, from the same seeds.

## Consequences
- **Same runs.** The whole runtime suite passes unchanged (326 tests), including the golden replays and the scenarios that compare runs with and without the head camera's video.
- **Smaller adapter.** `adapter.py` goes from 835 to 634 lines; each new file is under 200. The camera's noise and model, the grasp and the contact rules can now get their own unit tests; none was added in this change.
- **For M4.1.** `Videos` and `HeadCamera` take images from a renderer, so the hardware adapter can give them camera images instead of renders.
- **Not done.** The constructor still has 26 parameters. Grouping them into a configuration object would change every place that builds the adapter, so it waits for a change that needs it.
