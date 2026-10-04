# 0011: Robot time, terminal e-stop and latched safety stops

Status: Accepted · Date: 2026-10-04 · Milestone: M3+ (before M4)

## Context
A review before the first hardware milestone found that the safety layers behaved differently in fast simulation and on a robot. The monitor and the watchdog paced themselves on wall-clock time while a fast simulation runs many robot-seconds per wall-second, so the robot could cross a geofence by more than a metre before a check; the monitor busy-looped while waiting for an operator. An e-stop was invisible to the executor, which then offered "retry" on a stopped robot, an operator could answer "retry" forever, and some failure paths ended a run without stopping the robot. Capabilities the safety code relied on (speed cap, grasp feedback) were not part of the `RobotAdapter` port.

## Decision
Everything that paces itself on the robot uses a `Clock` port owned by the adapter: wall clock on hardware, simulated time in MuJoCo. The safety monitor checks every 0.1 s of robot time, the watchdog counts robot time, step deadlines and the plan budget are robot time (with a wall-clock hang guard). E-stop is terminal: adapters expose `estopped` and raise `RobotEstopped`, the executor ends the run with status `estop` and never retries. Leaving the geofence e-stops by default and is recorded once; a monitor that cannot check e-stops the robot. Safety paths act before they log. Operator retries are capped at three per step. The session stops the robot on any exception and still writes the episode. `set_speed_limit`, `GripResult`, `clock` and `estopped` are part of the port.

## Consequences
Simulation and hardware are checked with the same robot-time semantics, so the sim-to-real gate means the same thing on both, and a G1 adapter has an explicit list of what it must provide. A geofence breach now ends a run instead of being retried; scenes that relied on a plain stop must set `estop_on_geofence: false`. Run statuses gain `estop` and `error`, which readers of episodes must accept. The watchdog still lives in the same Python process as the heartbeat, so it protects against a stuck control loop, not against a frozen interpreter; for the real robot the hardware-side command timeout of the Unitree SDK must be the last line, and the M4 adapter has to configure it.
