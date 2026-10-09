# Lab safety checklist

The procedure for every session with a real robot (milestones M4.1 and M4.2 of the [runtime spec](runtime-spec.md), section 12). One copy per session, filled in and signed before the first motion command, kept with the episodes of that session. A session without a signed checklist is not a session: its runs do not count for the sim-to-real gate.

The checklist implements the layered safety of the spec (section 5) on the physical side: the runtime's own layers (plan validation, skill preconditions, the safety monitor, the watchdog, the terminal e-stop) assume that the layers below them, the hardware e-stop and the fenced area, are in place and tested. Standards to read before writing a site's own version: ISO 10218 (industrial robots), ISO 13482 (personal care robots), ISO 3691-4 (driverless industrial trucks, the closest match for a mobile robot moving goods indoors) and the EU Machinery Regulation 2023/1230.

## Roles

| Role | Who (name) | Duty |
|------|------------|------|
| Operator | | Runs the console, answers requests, presses the stop. Does nothing else during a run. |
| Spotter | | Watches the robot and the area, holds the hardware e-stop, calls "stop" out loud. Never the same person as the operator. |
| Visitors | | Outside the fence, always. |

## Before the session

| # | Check | Done |
|---|-------|------|
| 1 | The area is fenced or taped, at least 1.5 m beyond the geofence of the scene, with a clear floor, no stairs, no edges, no glass at robot height. | |
| 2 | Nobody is inside the fence while the robot is powered, except the spotter when a test needs it, and then at the far side of the robot's path. | |
| 3 | Hardware e-stop tested: press it, confirm the robot damps or stops, release, re-arm. Logged with the time. | |
| 4 | Battery above the scene's `min_battery_pct` plus the margin for the planned runs (a Go2 uses about 1 % per minute of walking; a G1 about 2 %). The charger is in the room. | |
| 5 | The scene file matches the room: locations measured and marked on the floor, obstacles listed, the geofence inside the fence, markers placed where the scene says. | |
| 6 | The runtime is started with recording on (`--zip`) and `--operator console`; the episode folder has free disk space for the session. | |
| 7 | The adapter reports the hardware command timeout it set (shorter than the 500 ms session watchdog); the session refuses to start otherwise. | |
| 8 | Network between the runtime and the robot checked (latency and packet loss within the adapter's limits); the operator's stop reaches the robot in under one second, measured. | |
| 9 | The plan has been run in simulation on the same scene file today, with the gate passed (`spingi bench --gate`). | |
| 10 | Everyone in the room knows the stop word and who holds the e-stop. Phones away from the operator and the spotter. | |

## During a run

- The operator starts a run only after the spotter says "clear".
- Any of these stops the run (operator Ctrl+C or spotter e-stop), no discussion: a person inside the fence, the robot within 0.5 m of the fence, an unexpected sound or motion, a lost connection, an answer from the robot that the operator does not understand.
- After an e-stop the robot stays stopped until the spotter has checked it and the area; the reason goes in the session log before the next run.
- The first run of a session is the inspection round at the lowest speed cap; the gate skills follow; nothing else runs before the gate skills pass that day.
- `wait_for_human` requests are answered by the operator only after the spotter confirms the robot is still.

## After the session

| # | Check | Done |
|---|-------|------|
| 1 | Every run is archived as an episode (zip), with the checklist scan and the session log in the same folder. | |
| 2 | Incidents, near misses and every e-stop are written down with the run id, even when nothing happened. | |
| 3 | Battery on charge, robot in damp mode or powered off, e-stop pressed while unattended. | |
| 4 | Runs counted toward the gate (section 8.4 of the spec): only the ones with the checklist signed and the recording complete. | |

## Sign-off

| Date | Site | Robot | Operator | Spotter | Runs | Incidents |
|------|------|-------|----------|---------|------|-----------|
| | | | | | | |

Signatures: operator ______________________  spotter ______________________
