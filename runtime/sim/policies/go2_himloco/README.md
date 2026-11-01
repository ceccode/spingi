# go2_himloco

HIMLoco walking policy for the Unitree Go2 (12 joints), from
[fan-ziqi/rl_sar](https://github.com/fan-ziqi/rl_sar), file `policy/go2/himloco/himloco.pt` with its
`config.yaml` (commit `376d42c9`; sha256 `d9945f85a39944aaf241a67f7d2b25a15326a0222d0e503aeb05a7f188430509`).
HIMLoco (Hybrid Internal Model, OpenRobotLab) is trained for blind walking on rough terrain: slopes, steps, uneven
ground.

- Networks: an estimator MLP 270 -> 128 -> 64 -> 19 reads the last six observation frames and returns the body
  velocity (3) and a terrain latent (16, normalised to unit length); the actor MLP 64 -> 512 -> 256 -> 128 -> 12
  reads the latest frame, the velocity and the latent. ELU between layers.
- Observation frame (45): velocity command scaled by (2, 2, 0.25), base angular velocity x 0.25, gravity in the
  body frame, joint positions relative to the default pose, joint velocities x 0.05, last action.
- Action: joint targets, default pose plus action x (0.125, 0.25, 0.25) per leg, tracked by a PD loop computing
  torque (kp 40, kd 1), at 50 Hz on a 200 Hz simulation.
- In MuJoCo it barely turns on the spot, so the simulator hands turning and slow moves to `go2_robot_lab`.

Modifications: the TorchScript archive's tensors were read as raw float32 and saved as plain arrays
(`policy.npz`), and the configuration was written as `policy.json`, by `runtime/scripts/import_policies.py`; the
numbers are unchanged. Licensed under the Apache License 2.0, see `LICENSE`.
