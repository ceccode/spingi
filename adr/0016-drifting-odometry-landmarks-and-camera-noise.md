# 0016: What the robot knows: drifting odometry, landmarks and a noisy camera

Status: Accepted · Date: 2026-11-01 · Milestone: M4.0 · Amends [0015](0015-physics-locomotion-and-outdoor-ground.md) · Answers Q14 for `navigate`, in simulation

## Context
Until now the runtime read the simulated robot's true pose and perfect camera frames. A real Go2 or G1 estimates its pose from its legs and IMU, and the estimate drifts by a few per cent of the distance walked and a little at every turn. Its camera blurs, adds noise and streams JPEG. A plan that works on the truth can send a real robot off the side of a ramp, or make it inspect the wrong spot while it reports success. Out of the box neither robot knows where anything is. Unitree's own navigation service needs the EDU version with an extra lidar and a map built beforehand, indoors and within 25 × 25 m. Spingi's plans already name places; what was missing is how the robot finds them again after it has drifted.

## Decision
**The pose is an estimate.**
- `get_pose` returns the robot's own estimate.
- A new port method, `relocalize(pose)`, corrects it. From then on, poses, `walk_to` targets and the camera's pose in a `Frame` are in the corrected frame.
- In physics mode, `SimAdapter(odometry="drift")` feeds the estimate a corrupted copy of the true motion:
  - scale errors drawn once per run: strides 3 %, sideways steps 5 %, turns 1 %;
  - a gyroscope bias of about 20° per hour;
  - random error that grows with the square root of the distance walked and the angle turned, and is worse on gravel and dirt.
- The start jitter becomes an error the robot does not know about.
- The kinematic base never drifts, but it follows corrections.

**Landmarks.**
- A scene's `landmarks` are AprilTags at known places, on posts or flat on walls (`marker_id`, `x`, `y`, `facing`, `height_m`, `size_m`, `post`). They are part of the `WorldState`.
- `localize` returns the tag's centre and the direction its face looks.
- `navigate` looks for the tags in view before it leaves and at every waypoint it reaches:
  - it takes three frames, and a tag must be seen in at least two of them;
  - the corrected poses are averaged;
  - a tag is used only within 10 times its side;
  - a correction larger than 2 m or 0.6 rad is refused as a misread;
  - when the corrected pose falls outside the waypoint, the robot walks the difference. At the destination the limit is half the tolerance, plus 0.15 rad of heading.
- A retried navigate resumes from the waypoint the robot stands at.

**A camera with noise.** `camera_noise`:
- blurs the frame by 0.7 px;
- varies the exposure;
- adds sensor noise that grows with brightness;
- compresses to JPEG at quality 80, which is how the Go2 streams its camera and what the episode stores;
- gives the robot a focal length about 1 % off.

`--realistic` turns on physics, drift, camera noise and marker perception together. At the end of every walk the simulator records `sim.arrival`: where the robot really stopped against where it believed it stopped. The bench reports the worst gap of each run, the corrections, the tags expected and not seen, and the anomalies the inspections report. The runtime never reads `sim.arrival`.

## Consequences
Plans now meet a robot that can be wrong about where it is, and the bench measures the consequence (runtime README).
- **Yard patrol, Go2, 30 runs.** Without its tags, 37 % of the runs succeed: the robot falls off the ramp in 3, and in most of the others it reports a gauge or a sign missing because it looks at the wrong spot, up to 2 m from where it believes it is. With the tags all 30 succeed, about 25 % slower, and the worst gap before a correction has a median of 0.4 m.
- **Inspection round and material runner.** Both pass every run, on both robots.

A scene is now also a plan of where its tags go. The rules were measured with a script:
- a tag is used within 10 times its side, so a 30 cm tag works up to 3 m;
- close inspection targets get two tags, one on either side;
- long legs get a tag every 8 to 10 m;
- tags are kept off the routes.

Physics runs take three frames at every stop. Episodes with a noisy camera store JPEG.

Defects found along the way and fixed:
- **Camera clipping**: the clipping planes were relative to the scene's size, so in the yard the head camera drew nothing within 47 cm.
- **Tracking camera**: objects carried mass and pulled the tracking camera off the robot.
- **Go2 manoeuvres**: HIMLoco stalls when stepping back at an angle, so the Go2's manoeuvring policy now takes every move that is not mostly forward.

The drift figures are assumptions from the literature on legged odometry, not measurements of a Go2, and must be replaced by the robot's own figures in the lab (M4.1). The hardware adapter will implement `relocalize` the same way: an offset over the robot's odometry.
