# 0010: LLM planner with structured output, validated like any plan

Status: Accepted · Date: 2026-10-04 · Milestone: M3

## Context
ADR-0003 says the language model proposes and the runtime disposes. The planner needs a reliable way to get a plan, not prose: tool use with a forced call is rejected on current Claude models (Opus 5.5 returns a 400 on `tool_choice` any/tool), and free-text JSON can drift from the skill whitelist. The planner must also be measurable, otherwise every prompt change is a guess.

## Decision
`LLMPlanner` calls Claude (default `claude-opus-5-5`, effort `medium`) with structured outputs: a JSON schema generated from the skill registry, one `anyOf` variant per skill with that skill's parameter schema, every object strict. Constraints structured outputs do not support are stripped from the schema and enforced afterwards by `validate_plan`. The model sees the request, the skill summaries and the symbolic world (locations, known objects, routes, battery), never telemetry. An invalid plan goes back once with the validation errors, in an append-only conversation; a second failure is an error and nothing runs. Refusals and truncation are errors. Server-side fallbacks (`fallbacks: "default"`) are on. Ten golden cases in `plans/golden/planner_cases.yaml` with an explicit equivalence rule measure the planner (`spingi eval-planner`), and a test checks that every golden plan runs in simulation.

## Consequences
The model cannot name a skill that does not exist or give a parameter the wrong shape, and anything it gets wrong in substance (an unknown location, a forward reference) is caught before execution. The planner is replaceable: another provider or a local model only has to return the same JSON. Routes in the scene file carry what a path planner would compute, until there is one. Every evaluation run calls the API and costs money, so it is a manual command, not part of CI; unit tests use a fake client. Recurring tasks should keep using static YAML plans: deterministic and free.
