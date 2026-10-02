# 0007: Predefined grasp on standard containers, simulated kinematically

Status: Accepted · Date: 2026-10-02 · Milestone: M2

## Context
Picking arbitrary objects with a learned policy needs hundreds of teleoperated demonstrations per task, GPUs to train, and a robot with working hands; public results on the G1 are around 50% success. The first useful tasks (material runner, kanban) only need standard containers with handles at known heights. The simulator does not model arm dynamics or contact (ADR-0006).

## Decision
`pick` and `place` are programmed, not learned: the arm moves to a predefined pose relative to the object, the gripper closes, and the adapter reports whether an object is held. In simulation the grasp is kinematic: when the gripper closes, the nearest object within reach attaches to the hand and follows the base; on release it is put down on the surface below the hand. The skills verify the result through the world state (holding, object pose) rather than trusting the command.

## Consequences
Level L2 tasks run end to end in simulation today and the runtime logic around manipulation (reach checks, retries, escalation, episodes with moving objects) is tested without a vision or manipulation model. Nothing is learned about real grasp success; that comes from the robot in M4. Learned policies, when they arrive, plug in behind the same `pick`/`place` contract. Contact-rich manipulation (doors, buttons, insertions) stays out of scope.
