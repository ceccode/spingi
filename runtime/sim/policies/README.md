# Walking policies

Learned walking controllers that physics locomotion (`spingi run --physics`, ADR-0015) runs in place of the robot
vendor's controller. Each folder holds one policy:

- `policy.npz`: the network's weights as plain arrays (`w0, b0, w1, ...`; `e_w0, ...` for an estimator;
  `obs_mean, obs_std` for an input normaliser). Inference is numpy only.
- `policy.json`: how the network was trained to be driven: joint order, gains, default pose, action scale,
  observation terms and scales, command limits.
- `LICENSE` and `README.md`: where it comes from and under which terms.

The files are produced by `runtime/scripts/import_policies.py` from the published originals and are committed, so a
checkout needs neither the original repositories nor PyTorch or ONNX.

| Folder | Robot | Used for | Origin | License |
|---|---|---|---|---|
| `g1_mjlab_velocity` | Unitree G1 | walking and turning | unitree_rl_mjlab, velocity policy v0 | Apache-2.0 |
| `go2_himloco` | Unitree Go2 | walking, on rough ground too | rl_sar, go2/himloco (HIMLoco) | Apache-2.0 |
| `go2_robot_lab` | Unitree Go2 | turning on the spot, slow moves | rl_sar, go2/robot_lab | Apache-2.0 |

These policies stand in for the vendor's controller; they are not it. The G1 policy was trained on flat ground only,
and it falls on kerbs and rough dirt that the real robot's controller may handle; the Go2 policies were trained in
other simulators and transferred to MuJoCo. What a run in physics shows is how the runtime copes with a robot that
walks, drifts, gets stuck and falls, not how well a specific Unitree firmware walks.
