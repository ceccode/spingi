# 0004: Plans and scenes as YAML data, without control flow

Status: Accepted · Date: 2026-10-01 · Milestone: M0

## Context
We could express tasks as Python code (maximum flexibility) or with a DSL with conditions and loops. Both make the plan hard to validate, to diff, and to have an LLM generate safely.

## Decision
A `TaskPlan` is an ordered list of steps, each with `skill`, `params` and an `on_failure` policy (`retry`, then `needs_human` / `abort` / `skip`). No `if`, no loops. The only link between steps is the `$<skill or index>.<field>` reference to the evidence produced by a previous step. Simulation scenes follow the same principle: declarative YAML that generates the scene.

## Consequences
Plans are versioned, diffed, reviewed in code review and tested as data. If a task requires a decision, the Planner generates a different plan starting from the current state; the plan itself does not branch. Automatic replanning arrives in v1 and goes through the same mechanism. Scenes are test data, versioned with the tests that use them.
