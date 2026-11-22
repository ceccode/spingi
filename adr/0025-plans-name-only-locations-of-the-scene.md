# 0025: A plan names only locations its scene has

Status: Accepted · Date: 2026-11-22 · Milestone: M4.0

## Context
`validate_plan` checked a plan against the skill registry and the robot's capabilities, never against the scene. A step that named a location the scene does not have was accepted and the run started; the skill's precondition found the mistake only when the robot reached that step, and the operator was asked to retry, skip or abort. `spingi run` of a plan with `navigate { to: pump_a }` on `sim/scenes/yard_outdoor.yaml`, which has `pump_A`, ended in OPERATOR NEEDED instead of INVALID_PLAN. In a long patrol a typo in the last step wastes the whole run, and the robot stops wherever it is. The LLM planner validated its plans without the world it had just described to the model, so a location the model invented passed as well, although ADR-0010 counts unknown locations among the mistakes validation catches.

## Decision
- **A skill declares which of its parameters name locations, in its `Params`.** `LocationName` and `LocationNames` (in `spingi/core/skill.py`) are `str` and `list[str]` with a marker that is pydantic metadata only, so the JSON schema the planner sees does not change; `Skill.location_params()` lists the marked fields. `navigate` marks `to` and `via`, `inspect` marks `target`, `place` marks `at`. Nothing in `plan.py` lists skills or parameter names.
- **A valid plan names only locations of its scene.** `validate_plan` takes the scene's location names (`locations=`) and rejects every name a location parameter gives that is not one of them, one error per name, with the closest known name when there is one (difflib, ignoring case): `step 0 (navigate): unknown location 'pump_a', did you mean 'pump_A'?`. A parameter whose whole value is a reference (`$navigate.reached`) is resolved at run time and is not checked. Without `locations`, names are not checked, as capabilities are not without a robot.
- **One check for both planners.** The Executor passes `state.locations` before the first step. The LLM planner passes the locations of the world it described to the model, so a wrong name goes back to the model in the validation round instead of reaching the robot.

## Consequences
- **Nothing moves.** A plan with a misspelled location ends as `invalid_plan` with the `plan.invalid` event before the first step: the robot stays where it is and nobody is asked.
- **The preconditions keep their own check.** A reference can resolve to a name the scene does not have, and a skill can be run without `validate_plan`; `navigate`, `inspect` and `place` still fail their precondition on an unknown location. A `$...` string inside a list (a `via` waypoint) is not a reference, so it is checked as a name.
- **A new skill must mark its location parameters**, or its locations are checked only when the robot reaches the step. A unit test lists the marked parameters of every skill.
- **Verified by tests.** Unit tests cover each marked parameter, the suggestion, references, the Executor ending a patrol with a typo in its last step before the first, and the LLM planner sending the error back; every plan in `plans/` and every golden planner case is checked against its own scene. Tests that used an unknown location to make a precondition fail now use a valid step the robot is out of place for.
