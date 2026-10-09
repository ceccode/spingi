# 0013: Locomotion comes from the vendor's controller; the simulator stays kinematic until a policy is needed

Status: Proposed (to be accepted before M4.1) · Date: 2026-10-09 · Milestone: M4.0

## Context
Question Q10 of the spec has been open since the first draft: does Spingi command the robot's walking through the vendor's controller, or train and run its own locomotion policy? And in simulation, does the base move kinematically (ADR-0006) or under a walking policy? The runtime only ever asks for `walk_to(pose, max_speed)` and `stop()`; everything below that is the robot's business. Unitree ships a locomotion controller on both the Go2 (the sport-mode API: velocity commands, stand up, sit down) and the G1 (the high-level locomotion client), reachable through `unitree_sdk2_python`. Public RL policies exist (`unitree_rl_gym`, legged-gym derivatives) but bring PyTorch, third-party weights and a controller to validate ourselves, with falls as the failure mode.

## Decision
The hardware adapter sends velocity commands to the vendor's controller and never commands joints. A learned policy is considered only if the vendor's controller fails the sim-to-real gate on a scene we need (stairs, a ramp, a floor it cannot handle), and then as a separate component below the adapter, with its own ADR. The simulator keeps the kinematic base: it measures routes, travel times, collisions and the runtime's logic, which is what the gate checks; a walking policy in MuJoCo would be added only to study terrain, never to test the runtime.

## Consequences
The adapter is thin, as the ports intend: `walk_to` is a loop that reads the pose and streams velocity commands clamped by the speed cap, with the vendor's balance and gait underneath. Falls, stairs and rough terrain are the vendor's claims, verified in the lab (M4.1) and not in simulation; the lab checklist has a flat, clear floor as its first item for this reason. No PyTorch in the runtime's dependencies. If a policy ever enters, it does so behind `walk_to`, and the contract tests and the gate stay the same.
