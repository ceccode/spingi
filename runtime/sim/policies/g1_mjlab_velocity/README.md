# g1_mjlab_velocity

Velocity-tracking walking policy for the Unitree G1 (29 joints), from
[unitreerobotics/unitree_rl_mjlab](https://github.com/unitreerobotics/unitree_rl_mjlab), file
`deploy/robots/g1/config/policy/velocity/v0/exported/policy.onnx` (commit `1425b15f`, training run
2026-03-18_18-40-20; sha256 `2a66ca6336eadb3c0b34b557763f3e06d01ff8fcf6260dd4cedbd69d6093fc28`).

- Network: MLP 98 -> 512 -> 256 -> 128 -> 29 with ELU, after an observation normaliser.
- Observation: base angular velocity, gravity in the body frame, velocity command (vx, vy, wz), gait phase
  (sin, cos; period 0.6 s; zero when standing), joint positions relative to the default pose, joint velocities,
  last action.
- Action: joint position targets, default pose plus action times scale, tracked by position servos with the trained
  stiffness and damping, at 50 Hz on a 200 Hz simulation.
- Trained on flat ground (the actor reads no height scan).

Modifications: the ONNX graph was converted to plain arrays (`policy.npz`) and its metadata to `policy.json` by
`runtime/scripts/import_policies.py`; the numbers are unchanged. Licensed under the Apache License 2.0, see
`LICENSE`.
