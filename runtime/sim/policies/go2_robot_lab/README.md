# go2_robot_lab

Velocity-tracking walking policy for the Unitree Go2 (12 joints), trained with robot_lab, from
[fan-ziqi/rl_sar](https://github.com/fan-ziqi/rl_sar), file `policy/go2/robot_lab/policy.pt` with its
`config.yaml` (commit `376d42c9`; sha256 `9f14cb95e74ac9e5e30954da0fc0c33eaa41e337852ebed19fcee869279ade0b`).

- Network: MLP 45 -> 512 -> 256 -> 128 -> 12 with ELU.
- Observation: base angular velocity x 0.25, gravity in the body frame, velocity command, joint positions relative
  to the default pose, joint velocities x 0.05, last action. Joint order FR, FL, RR, RL.
- Action: joint targets, default pose plus action x (0.125, 0.25, 0.25) per leg, tracked by a PD loop computing
  torque (kp 20, kd 0.5), at 50 Hz on a 200 Hz simulation.
- In MuJoCo it tracks turning on the spot well but drifts sideways when walking straight, so the simulator uses it
  for turning and slow moves only; `go2_himloco` walks.

Modifications: the TorchScript archive's tensors were read as raw float32 and saved as plain arrays
(`policy.npz`), and the configuration was written as `policy.json`, by `runtime/scripts/import_policies.py`; the
numbers are unchanged. Licensed under the Apache License 2.0, see `LICENSE`.
