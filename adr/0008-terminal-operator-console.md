# 0008: Operator console v0 in the terminal

Status: Accepted · Date: 2026-10-04 · Milestone: M2

## Context
The runtime is supervised by default (principle P7): when a step needs a human, someone must answer retry, skip or abort, and someone must be able to stop the robot at any time. A web console would add a server, authentication and a second process to keep in sync with the run. Today the operator sits next to the robot or the laptop running the simulation; remote supervision comes with real deployments.

## Decision
The v0 console is the terminal. `spingi run --operator console` subscribes a reporter to the event log that prints one line per meaningful event, and answers `human.request` with a prompt that accepts only retry, skip or abort. Ctrl+C is the stop button: it emits `operator.stop`, e-stops the robot, cancels the run and still writes the episode. The console is one more `HumanGateway` and one more event-log subscriber; the core knows nothing about it.

## Consequences
Supervised runs work today with no extra service, and the same contracts serve a web or remote console later: it will implement `HumanGateway` and subscribe to the event log. The terminal console cannot supervise a run on another machine. A prompt that times out leaves its `input()` thread waiting for Enter; the run has already moved on, and this is documented. The keyboard e-stop does not replace the hardware e-stop (safety layer S0).
