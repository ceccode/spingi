# 0014: The runtime runs on a laptop in the lab, on the robot's computer for a pilot

Status: Proposed (to be accepted before M4.1) · Date: 2026-10-09 · Milestone: M4.0

## Context
Question Q11 of the spec: where does the Spingi process run? On the robot's on-board computer (a Jetson Orin on the G1 EDU, the Go2 EDU's on-board Jetson or a Raspberry-class board on the base model) or on a laptop talking to the robot over the network? The runtime is pure Python with asyncio, needs no GPU, and talks to Unitree robots through DDS (CycloneDDS under `unitree_sdk2_python`), which works over a wired or Wi-Fi link as well as on-board. The in-process watchdog (ADR-0011) protects against a stuck control loop; a frozen process, a dropped link or a crashed laptop are handled by the robot's own command timeout, which the adapter must set (spec section 3.5, M4.0).

## Decision
In the lab (M4.1, M4.2) the runtime runs on a Linux laptop wired to the robot, with the operator console on the same machine: fastest to debug, easiest to record, and the laptop is where the simulator already runs, so a scene can be benchmarked and then run on the robot from the same prompt. The adapter configures the robot's command timeout shorter than the session watchdog and refuses to start otherwise, so a dead laptop stops the robot within that timeout. For a pilot outside the lab the runtime moves on-board, unchanged: the process is the same, the console connects over the network, and the episode is written on the robot and synced afterwards.

## Consequences
Lab sessions measure link latency and loss as part of the checklist (item 8), because the stop path crosses the network. The adapter is written once and does not know where it runs; nothing in the runtime assumes a GPU or a particular board. Moving on-board for a pilot is a deployment task (packaging, service, log sync), not a runtime change, and gets its own checklist. Wi-Fi in the lab is not used for the stop path: wired, or a dedicated access point.
