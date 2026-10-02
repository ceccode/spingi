# 0001: No ROS2 dependency in the core

Status: Accepted · Date: 2026-10-01 · Milestone: M0

## Context
ROS2 is the de facto standard in robotics and the Unitree ecosystem supports it. It solves problems we do not have in v0, though: many processes, many languages, nodes distributed across several machines. The cost is high: setup, build system, DDS, difficulty of unit testing in CI, versions tied to Ubuntu. The Comenius University paper on the G1 points to version drift between SDK, ROS and firmware as the platform's main weakness.

## Decision
The `spingi.core` package does not import ROS2 or any robotics framework. The core is pure Python with `asyncio`. If a ROS2 node is ever needed (Nav2, a driver), it will live in an adapter or in a separate process behind the `RobotAdapter` interface.

## Consequences
The whole core runs in CI without a GPU, without a robot and without special containers. People who know ROS2 will not find their tools in the core and have to go through an adapter. An architecture test (`tests/test_architecture.py`) verifies that `spingi.core` does not import `spingi.adapters`, `spingi.skills`, `spingi.planner`, `spingi.perception`. If the core ever needs ROS2, a new ADR is written that supersedes this one.
