# 0009: Episodes stay the source of truth; LeRobot v3.0 is an export

Status: Accepted · Date: 2026-10-04 · Milestone: M2

## Context
LeRobotDataset is the de facto open format for robot learning data, and v3.0 (files with many episodes, Parquet plus MP4, metadata in `meta/`) is what LeRobot tools load. Spingi episodes carry what LeRobot does not model: the plan, skill outcomes, operator requests, safety events, sparse inspection frames. Making LeRobot the native format would lose that information; ignoring it would cut Spingi off from the training tools.

## Decision
The Spingi episode (docs/episode-format.md) remains the source of truth. `spingi export lerobot` derives a LeRobotDataset v3.0 folder from one or more episodes: the trajectory resampled at a fixed rate (default 10 Hz), `observation.state` and `action` as base pose plus gripper state, the action being the next state, the plan description as the task, `next.done` and `next.success` from the episode outcome. The layout and keys follow lerobot's own metadata code and published v3.0 datasets. Images are not exported until an adapter records the head camera at a fixed rate. Verified on 2026-10-04: a dataset exported from two simulated episodes loads with `LeRobotDataset` from lerobot 0.6.1 (frames, episodes, fps, features and task text as expected).

## Consequences
Episodes can feed LeRobot training and visualisation tools, and the exporter is the only place that knows the LeRobot layout, so a v4 means one new exporter. The export is lossy on purpose: plans and events stay in the episode. The current state and action only describe base navigation and gripper; joints and images come with real locomotion and fixed-rate camera recording. The export depends on pandas and pyarrow, kept in an optional `export` extra.
