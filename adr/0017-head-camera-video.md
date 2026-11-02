# 0017: What the robot sees, continuously: the head camera as a video

Status: Accepted · Date: 2026-11-02 · Milestone: M4.0 · Extends the episode format (0.1, without a version change)

## Context
An episode shows the robot from the outside, plus the frames the runtime took: one per `detect` or `inspect`, three at each landmark stop. Between two frames nobody sees what the camera saw. That is the question people ask first about a robot ("what does it see?"), and it is the first thing to check when a run goes wrong: whether the robot faced the wrong way, or whether the tag was hidden behind a kerb. On the real Go2 the camera streams video all the time. An episode should keep that view too, and the viewer should play it in step with the replay. The cost: the simulator must render the head camera at a fixed rate, and a render takes about 15 ms on a laptop GPU, while a physics tick takes 0.3 ms.

## Decision
- **Recording.** `spingi run --camera-video` (`SessionConfig.record_camera`, sim adapter) renders the head camera every 5 ticks, 10 frames per second of simulated time, at 640 × 480. The frames go into `head.mp4` (H.264, rate factor 35, a keyframe every 2 s), written frame by frame and never kept in memory. It is off by default: it makes a run 3 to 5 times slower, and `spingi bench` writes no episodes.
- **On the timeline.** The manifest's `camera_video` (`file`, `camera`, `fps`, `start_t`, `frames`, `width`, `height`) puts frame i at `start_t + i / fps`, on the same axis as the trajectory and the events. A frame that cannot be rendered ends the video: a gap would shift every frame after it.
- **The clean render.** The video shows the scene from where the camera really is, without camera noise. With `--camera-noise`, the frames the robot takes and analyses carry the noise. Adding the noise to the video would double the run time and grow the file, and the video is for people, not for the runtime. Recording the video changes nothing in the run: it draws no random numbers, and a test checks that pose, estimate and frames are the same with and without it.
- **In the viewer.** The video plays in an inset over the 3D view, labelled "robot's view". It plays along at the replay's speed and is sought only when it drifts by more than 0.3 s of video. Paused, it shows the last frame taken at or before the replay's time. A click enlarges it. The viewer refuses a malformed `camera_video` entry. A video left out of the archive leaves the replay without it. The site's Content Security Policy allows media from `blob:` URLs only, besides its own.

## Consequences
- **Seeing the gap between belief and truth.** The inset shows what the camera really saw, while the orange outline shows where the robot believed it was. When the two disagree, the gap is visible.
- **Size.** About 1 MB per minute outdoors, less indoors: the yard patrol sample (184 s) grows from 0.9 to 3.6 MB. The viewer's samples carry their video.
- **Not changed.** The third-person `run.mp4` (`--record`) stays outside the viewer. The LeRobot export does not read `head.mp4` yet; it is the fixed-rate image stream that export was waiting for (ADR-0009).
- **On the robot.** The hardware adapter (M4.1) will record the robot's own camera stream into the same file and manifest entry.
