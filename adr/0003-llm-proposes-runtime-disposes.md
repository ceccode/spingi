# 0003: The LLM proposes a declarative plan, the runtime executes it

Status: Accepted · Date: 2026-10-01 · Milestone: M0

## Context
A language model is the most natural way to turn "bring the red box from A to B" into actions. But an LLM is not deterministic, cannot be tested like code and cannot be a safety layer. Giving an LLM access to joints, speeds or torques means that a generation error moves a 35 kg robot.

## Decision
The LLM produces only a `TaskPlan`: a list of skills taken from a whitelist (the registry), with parameters validated against each skill's schema. The plan is validated before it starts, like any hand-written plan. The Planner does not see raw telemetry, frames or joints: it sees the symbolic state. Speed limits, geofences and preconditions live in the runtime, and the LLM cannot modify them.

## Consequences
The Planner is replaceable and testable by comparison against "golden" plans. A static YAML plan and an LLM-generated plan go through the same Executor: the execution logic is tested once. If the LLM generates an invalid plan, the runtime rejects it and asks a human; it does not "give it a try". The cost is that the vocabulary of possible actions is limited to the skills we have written and tested: this is intentional.
