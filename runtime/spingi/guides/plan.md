# Writing a plan

A plan is a YAML list of steps. Each step names a skill, its parameters and what to do if it fails. No branches, no loops: a plan is data. Check it with `validate_plan`, run it with `run_plan`.

```yaml
id: fetch_crate                       # letters, digits, _ and -
description: "Fetch the crate from rack A and bring it to the bench"
deadline_s: 300                       # optional: robot-time budget for the whole run
steps:
  - skill: navigate
    params: { to: rack_A }
    on_failure: { retry: 2, then: needs_human }
  - skill: detect
    params: { cls: crate, expect: 1 }
    on_failure: { retry: 2, then: needs_human }
  - skill: pick
    params: { object_id: "$detect.objects[0].id" }     # from the detect step's result
  - skill: navigate
    params: { to: bench }              # add via: [waypoint, ...] when the straight leg crosses something
  - skill: place
    params: { at: bench }
```

## Skills

`list_skills` gives the exact parameters for a robot. In short:

| Skill | Parameters | Needs | What it does |
|-------|------------|-------|--------------|
| `navigate` | `to`, `via: []`, `max_speed: 0.5` | locomotion | Walks to a location, through the `via` waypoints in order, in straight lines |
| `detect` | `cls`, `expect: 1` | camera | Looks for objects of a class from where the robot stands; fails if it finds fewer than `expect` |
| `inspect` | `target`, `checks: []` | camera | Takes a frame at a location and evaluates `present:<cls>` and `absent:<cls>`; reports anomalies, does not fail on them |
| `pick` | `object_id`, `arm: right` | arm | Takes an object whose position is known and within 0.9 m |
| `place` | `at`, `arm: right` | arm | Puts down what it holds; the robot must be at `at` |
| `wait_for_human` | `prompt`, `timeout_s: 300` | - | Stops and waits for a person to answer |
| `say` | `text` | - | Writes a line in the event log |

The Go2 has locomotion and a camera, no arm. The G1 has all three.

## Rules the check enforces

- Only locations of the scene: `to`, `via`, `target` and `at` must be names `describe_scene` lists. Never invent one.
- Only the declared parameters: a misspelled one is an error, not a default.
- Only skills the robot can run.
- A reference `"$<skill or step index>.<field>"` is the whole value of a parameter and points to an earlier step: `"$detect.objects[0].id"` is the id of the first object the latest `detect` found.

## What makes a plan work

- **Go there before you look.** `inspect` needs the robot at its `target`: put a `navigate` to that location first. `detect` looks from where the robot stands.
- **Detect before you pick.** `navigate` to where the object is, `detect` its class, `pick` it by reference. `place` needs the robot at the destination first.
- **Straight lines.** If the leg from where the robot is to `to` crosses an obstacle, list the waypoints in `via`. The scene's `routes` say which. `validate_plan` warns about a leg that crosses a box.
- **Failure policy.** `on_failure: { retry: N, then: needs_human | abort | skip }`. Navigation and detection can fail for a moment: give them `retry: 2`. Use `skip` only for a step the task can live without, such as one stop of a patrol.
- **Speed.** `max_speed` asks; the scene's `safety.max_speed` caps. Slow down (0.5 to 0.6) on rough ground, ramps and kerbs.
- **Time.** A `navigate` has 60 seconds unless the step has its own `deadline_s`, and the plan's `deadline_s` is the budget of the whole run. A robot that walks takes up to twice the distance over the speed: 19 m at 0.6 m/s take about 35 s gliding and about 50 s walking. Give a long `navigate` a `deadline_s`, and the plan twice its walking time. `validate_plan` warns when a budget is tight.
- **Keep it minimal.** No `say` unless the person asked for an announcement; no `wait_for_human` unless a person must do or confirm something.
- **If it cannot be done** with these skills, locations and objects, say so to the person instead of writing a plan that pretends.

## Outcomes of a run

`success`, `aborted` (a step failed and the answer was abort), `invalid_plan` (refused by the check, nothing moved), `deadline`, `estop` (a safety stop or a fall: the run ends, nothing is retried), `error`.

When a run does not succeed, the result says which step, how many attempts and why. Typical reasons and fixes:

| Reason | Fix |
|--------|-----|
| `found 0 '<cls>', expected 1` | The robot looks from the wrong place or the wrong way: check the stop's position and `yaw`, or go nearer |
| navigation stopped short, an obstacle in the way | Add a waypoint in `via` |
| left the geofence, `estop` | A location or a leg too close to the fence |
| the robot fell (`physics`, `realistic`) | Slower `max_speed`, a gentler route, or a ground this robot cannot walk |
| `deadline` in `physics` or `realistic`, after a success in `kinematic` | Walking is slower than gliding: raise the plan's `deadline_s`, give the long `navigate` its own |
| an inspection that finds nothing in `realistic` and found it in `kinematic` | The object has no `marker_id`: the camera recognises objects by their tag |
| `anomalies present:<cls>` in an inspection | The check found the object missing: the report of the inspection, not a failure of the plan |
