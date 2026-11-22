# 0026: An unknown key in a plan is an error, never a default

Status: Accepted · Date: 2026-11-22 · Milestone: M4.0

## Context
Plans are data written by people and by a model (ADR-0004, ADR-0010), and pydantic models ignore keys they do not declare unless told otherwise. A misspelled parameter was dropped without a word: `navigate` with `params: { to: shelf_A, max_sped: 0.8 }` passed `validate_plan`, `spingi run` reported `SUCCESS`, and the robot walked at the default 0.5 m/s instead of 0.8. The same held for the plan's own keys: a step with `on_failre: { retry: 2, then: abort }` got the default failure policy, `needs_human` with no retry, and the plan never said so. A setting that silently falls back to its default on a robot is a safety problem: a speed limit, a retry budget or a deadline that the author believes is in force is not. Meanwhile the schema the LLM planner is constrained by already had `additionalProperties: false` on every object, so the model was held to a stricter rule than the files people write.

## Decision
- **Every skill's parameters refuse unknown keys.** `SkillParams`, in `spingi.core.skill`, sets `extra="forbid"`, and every skill's `Params` derives from it. `SkillRegistry.register` refuses a skill whose `Params` would accept an undeclared key, so a new skill cannot be lax by accident.
- **So do the plan's own models.** `TaskPlan`, `Step` and `OnFailure` set `extra="forbid"`.
- **Short errors.** `validate_plan` reports a misspelled parameter as `step 0 (navigate): invalid parameters: max_sped: Extra inputs are not permitted`, and the run ends as `invalid_plan` before anything moves. `load_plan` reports a plan that is not a `TaskPlan` as one line, `invalid plan (<path>): steps.0.on_failre: Extra inputs are not permitted`, instead of pydantic's multi-line message.
- **The CLI says it, not a traceback.** `spingi run` and `spingi bench` end such a file with `INVALID_PLAN · <the error>` and exit status 1, before a robot is started or a run folder is made. The operator console prints the `plan.invalid` event, so a refused plan says why there too.

## Consequences
- **No plan changes.** The four plans in `runtime/plans`, the ten golden planner cases, the plans of the four golden episodes and the test fixtures all validate unchanged.
- **The planner's schema does not change.** The JSON schema sent to the model is byte-identical for every robot, because `plan_schema` already built strict objects; only `SkillRegistry.schemas()`, the raw pydantic schemas, now carry `additionalProperties: false`. `spingi skills` prints the same. If a model answers with a key the schema does not allow, it is sent back as a validation error like any other.
- **Older episodes with a misspelled key no longer replay the same.** `spingi replay` loads the episode's `plan.yaml` again: a misspelled parameter now ends the replay as `invalid_plan`, reported `DIFFERENT`; a misspelled plan key is reported `INVALID` with the error, and the next episode is replayed. None of the golden episodes has one.
- **20 new tests**: a misspelled parameter on every skill, misspelled plan, step and failure-policy keys, the one-line `PlanError`, the registry refusing a lax skill, the CLI ending as `INVALID_PLAN` for both kinds of error, replay going on past an invalid episode, the console printing why a plan was refused, and the LLM planner sending a misspelled key back. Each fails when its guard is removed.
