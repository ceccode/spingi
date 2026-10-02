# 0006: Kinematic SimAdapter in MuJoCo with the G1 model from mujoco_menagerie

Status: Accepted · Date: 2026-10-02 · Milestone: M1

## Context
To watch the runtime drive a robot in 3D, measure realistic timings and produce images for perception, we need a simulator and a G1 model. MuJoCo runs on CPU, including on macOS, and in CI; Isaac Sim does not. Making the G1 actually walk requires a locomotion controller: Unitree's is not available in simulation, and the public ones (unitree_rl_gym) bring in PyTorch and third-party weights. That decision is still open (ADR-002 in the spec).

## Decision
`SimAdapter` loads the G1 from mujoco_menagerie (vendored in `sim/models/unitree_g1`, BSD-3 license) and moves the **base kinematically**: at every 20 ms tick it moves the pelvis pose toward the target at the requested speed, with clamping, a final rotation to the target yaw, and the legs held in the "stand" pose. Physics is used only for collisions: if a robot geom touches an obstacle, the robot goes back to the previous tick and stops, as a controller with obstacle detection would. The head camera added to the model renders frames for perception; a third-person camera records the video of the run. Scenes are generated from the declarative YAML.

## Consequences
The runtime, the skills, the scenario tests and perception are developed on a robot with the dimensions, footprint and camera of the G1, with realistic travel times, in CI and without a GPU. Nothing is measured about walking: stability, falls, stairs and real power consumption stay out of scope until locomotion is decided (future ADR: pre-trained policy in MuJoCo or vendor controller on the real robot). The arms do not move: pick and place in M2 will start with kinematic displacement of objects, then IK. The vendored model weighs 34 MB of meshes: acceptable; if it grows, it moves to an asset downloaded at setup.
