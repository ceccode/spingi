# 0005: Append-only JSONL event log as the single source of truth

Status: Accepted · Date: 2026-10-01 · Milestone: M0

## Context
Three things are needed from the same data: debugging a run, operational metrics (success rate, human interventions per 100 tasks, time per task) and an archive of episodes reusable as a dataset for imitation learning. Instrumenting three times would be costly and inconsistent.

## Decision
Every run writes structured events to an append-only JSONL file (`runs/<run_id>/events.jsonl`): run start and end, steps and skills, state deltas, perception results, safety events, human requests and responses, adapter calls and errors. The Recorder adds frames and state snapshots. Metrics are computed from the log, never from separate counters.

## Consequences
What is not in the log did not happen: every component that does something relevant emits an event. Tests can assert on events instead of internal state. A recorded run can be replayed (golden episode). The format is our own; an exporter to `LeRobotDataset` will arrive in M2 (open ADR). The log can grow: the Recorder subsamples frames, not events.
