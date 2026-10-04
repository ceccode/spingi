# Physical Agent Runtime: Specification v0.1

Status: v0.1, M0 to M3 implemented · Date: 2026-10-01, aligned with the code on 2026-10-04

Parts that are specified but not built yet are marked *(not implemented yet)*.

> A minimal runtime that turns a humanoid robot into a **reliable executor of simple physical tasks**, programmable by an AI agent but never directly controlled by it. Designed to be developed and tested entirely in simulation before touching the robot.

---

## 0. Principles (non-negotiable)

| # | Principle | What it means in practice |
|---|-----------|---------------------------|
| P1 | **KISS** | No robotics framework in the core. Pure Python, few abstractions, each with a reason. If something can be done with a function, it is not done with a class. |
| P2 | **Sim-first** | Every feature is born and passes its tests in MuJoCo before running on the robot. The real robot is an adapter like any other. |
| P3 | **Testable without hardware** | Every component has a test that runs in CI without a GPU and without a robot. Real adapters are the only components that cannot be tested in CI, and they are as thin as possible. |
| P4 | **The LLM proposes, the runtime disposes** | The LLM produces a declarative plan made of whitelisted skills with validated parameters. It has no access to joints, speeds, torques. Ever. |
| P5 | **Independent safety layers** | No single software component is the only barrier. Hardware e-stop, watchdog, geofence, speed limits, skill preconditions: each layer works even if the others fail. |
| P6 | **Everything is observable and recorded** | Every execution produces a structured event log and a reproducible episode. What is not in the log did not happen. |
| P7 | **Supervised by default** | The runtime starts in supervised mode (a human can stop everything at any time). Unsupervised autonomy is a feature unlocked per skill, with numerical evidence. |
| P8 | **Thin adapters, thick core** | The logic lives in the core. Adapters only translate. If an adapter contains task logic, it is an architectural bug. |

---

## 1. Scope and non-goals

### 1.1 In scope (v0)

- One robot at a time.
- **Sequential** tasks, composed of whitelisted skills.
- **Known and mapped** environment: named locations, known objects (markers or predefined classes).
- Two targets: `SimAdapter` (MuJoCo) and `UnitreeG1Adapter` *(not implemented yet, M4)*. A third adapter, `FakeAdapter`, is in-memory and serves unit tests.
- v0 skills: `navigate`, `detect`, `inspect`, `pick`, `place`, `wait_for_human`, `say`.
- Optional LLM Planner: recurring tasks use static YAML plans.
- Minimal operator console: live status, stop, answers to operator requests (`retry`, `skip`, `abort`, `continue`).

### 1.2 Non-goals (v0)

- Multi-robot, fleet coordination.
- Reinforcement Learning *inside* the runtime. Trained policies (locomotion, grasp) are consumed as black boxes behind a skill or an adapter.
- ROS2 in the core. A `Ros2BridgeAdapter` may exist, but it is optional and not a dependency.
- Navigation in unknown environments (live SLAM). v0 uses prebuilt maps.
- Conversational voice interaction. `say` is a feedback primitive, not a dialogue.
- Contact-rich manipulation (doors, buttons, insertions). Arrives in v1+ as separate skills.

---

## 2. Architecture

```
┌────────────────────────────────────────────────────────────────────────┐
│  Interfaces           CLI · chat · webhook · operator console           │
└───────────────┬────────────────────────────────────────────────────────┘
                │ natural-language request  or  TaskPlan YAML
                ▼
┌────────────────────────┐        ┌────────────────────────────────────┐
│  Planner               │        │  Skill Registry (whitelist)        │
│  LLM structured output │◄───────│  parameter schema for each skill   │
│  or StaticPlanner      │        └────────────────────────────────────┘
└───────────────┬────────┘
                │ TaskPlan (validated, JSON)
                ▼
┌────────────────────────────────────────────────────────────────────────┐
│  Task Executor                                                          │
│  step → precondition → execute(skill) → postcondition → next / recover  │
└───────┬────────────────────────┬──────────────────────┬────────────────┘
        │                        │                      │
        ▼                        ▼                      ▼
┌───────────────┐   ┌─────────────────────┐   ┌──────────────────────────┐
│  World State  │   │  Skills             │   │  Safety Monitor          │
│  locations    │   │  navigate · detect  │   │  watchdog · geofence     │
│  objects      │   │  pick · place       │   │  speed cap · e-stop      │
│  robot pose   │   │  inspect · wait     │   │  (can stop everything)   │
│  battery      │   └──────────┬──────────┘   └──────────────────────────┘
└───────────────┘              │
        ▲                      ▼
        │           ┌─────────────────────┐   ┌──────────────────────────┐
        │           │  Perceiver          │   │  Event Log / Recorder    │
        └───────────│  ground truth (sim) │   │  JSONL + frames + state  │
                    │  markers, VLM later │   │  → replay, dataset       │
                    └──────────┬──────────┘   └──────────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  RobotAdapter port  │
                    └──┬──────────────┬───┘
                       │              │
             ┌─────────▼───┐   ┌──────▼──────────────┐
             │ SimAdapter  │   │ UnitreeG1Adapter    │
             │ MuJoCo      │   │ unitree_sdk2_python │
             └─────────────┘   └─────────────────────┘
```

Implemented today: the CLI and the terminal operator console as interfaces, `StaticPlanner` and `LLMPlanner`, the Executor, seven skills, the Safety Monitor, `FakePerceiver` and `SimPerceiver` (ground truth from the scene), the event log and episode writer, `FakeAdapter` and `SimAdapter`. Not implemented yet: chat and webhook interfaces, marker and detector perception, the VLM, `UnitreeG1Adapter`.

### 2.1 Responsibilities

| Component | Does | Does not |
|-----------|------|----------|
| **Planner** | Turns a request into a valid `TaskPlan`. | Does not execute anything. Does not know the hardware. |
| **Task Executor** | Runs the plan's steps one at a time, handles retries and escalation. | Does not decide *what* to do. Does not move the robot directly. |
| **Skill** | Performs an atomic action with verifiable preconditions and postconditions. | Does not call other skills. Does not talk to the LLM. |
| **World State** | Single source of truth about locations, objects, robot. | Is not a database. It is a serializable in-memory object. |
| **Perceiver** | Answers "what do you see?" and "where is X?". | Does not decide what to do with what it sees. |
| **Safety Monitor** | Watches telemetry and state; can stop the robot independently of the Executor. | Does not plan. Does not recover. It just stops. |
| **RobotAdapter** | Translates abstract commands into SDK/sim calls and sensor readings into common structures. | No task logic. No retries. |
| **Event Log / Recorder** | Records everything, append-only. | Does not interpret. |
| **Operator Console** | Shows the run as it happens, stops the robot (Ctrl+C), answers operator requests with one of the offered options. | Is not a teleoperation app (v0). |

### 2.2 Why no ROS2 in the core

ROS2 solves problems we do not have in v0: many processes, many languages, many distributed nodes. The cost is high: setup, build system, DDS, difficulty of unit testing. The core stays pure Python with `asyncio`. If a ROS2 node is ever needed (e.g. for Nav2 or for a driver), it lives in an adapter or in a separate process with a narrow interface. This decision is recorded as ADR-0001.

---

## 3. Core contracts

All types are `pydantic.BaseModel` (validation and JSON serialization for free). The code below is normative for the signatures and indicative for the implementation; the source of truth is `runtime/spingi/core/`.

### 3.1 Base types

```python
class Pose2D(BaseModel):
    x: float
    y: float
    yaw: float = 0.0    # rad

class Pose3D(BaseModel):
    x: float; y: float; z: float
    qx: float = 0.0; qy: float = 0.0; qz: float = 0.0; qw: float = 1.0

class Location(BaseModel):
    name: str           # "shelf_A", "workstation_B"
    pose: Pose2D
    tolerance_m: float = 0.15

class ObjectRef(BaseModel):
    id: str             # "red_box_01"
    cls: str            # "red_box"
    pose: Pose3D | None = None
    confidence: float = 0.0
    marker_id: int | None = None   # fiducial marker; AprilTag detection is not implemented yet
```

### 3.2 World State

```python
class RobotState(BaseModel):
    pose: Pose2D
    battery_pct: float = 100.0
    holding: ObjectRef | None = None
    mode: Literal["idle", "walking", "manipulating", "estop"] = "idle"

class WorldState(BaseModel):
    locations: dict[str, Location] = {}
    objects: dict[str, ObjectRef] = {}
    robot: RobotState
    ts: float = 0.0

class StateDelta(BaseModel):           # what a skill proposes; applied by the Executor
    robot_pose: Pose2D | None = None
    robot_mode: RobotMode | None = None
    battery_pct: float | None = None
    holding: ObjectRef | None = None
    clear_holding: bool = False
    objects_upsert: dict[str, ObjectRef] = {}
    objects_remove: list[str] = []
    ts: float | None = None
```

Rules:
- The state is updated **only** by the Executor, with `apply_delta(state, delta)` (pure: it returns a new state), after a skill succeeds and its postconditions pass on the candidate state. No skill modifies the state directly: it returns a `StateDelta`. Perception results enter the state the same way: `detect` and `inspect` put the objects they see in `objects_upsert`.
- It is JSON-serializable at any time. The simplest test in the world: `WorldState.model_validate(state.model_dump())`.
- The initial state of a run comes from the scene file (`spingi.scenes.load_world`).

### 3.3 Skill

```python
class SkillOutcome(StrEnum):
    SUCCESS = "success"
    RECOVERABLE = "recoverable"   # retry, possibly with different parameters
    NEEDS_HUMAN = "needs_human"   # stop and ask
    FATAL = "fatal"               # stop everything, do not retry

class SkillResult(BaseModel):
    outcome: SkillOutcome
    delta: StateDelta | None = None
    reason: str = ""
    evidence: dict[str, Any] = {}   # e.g. {"frame_id": "...", "objects": [...]}; later steps can reference it

class Check(BaseModel):
    ok: bool
    reason: str = ""

class SkillContext(BaseModel):
    state: WorldState
    robot: RobotAdapter
    perceiver: Perceiver
    log: EventLog
    human: HumanGateway | None = None   # used by wait_for_human
    deadline_s: float

class Skill:                            # base class
    name: ClassVar[str]
    Params: ClassVar[type[BaseModel]]   # parameter schema, also what the planner sees
    default_deadline_s: ClassVar[float] = 30.0

    def preconditions(self, params, state: WorldState) -> Check: ...
    async def execute(self, params, ctx: SkillContext) -> SkillResult: ...
    def postconditions(self, params, state: WorldState) -> Check: ...
    async def abort(self) -> None: ...
```

The `SkillRegistry` is the whitelist: `default_registry()` registers the seven v0 skills, `names()` lists them and `schemas()` returns the JSON schema of each `Params`.

Rules:
- `preconditions` and `postconditions` are **pure**: they read the state and do not touch the robot. Testable with a hand-built `WorldState`.
- `execute` is the only place where `RobotAdapter` and `Perceiver` are called.
- Every skill has a `default_deadline_s`; a step can override it with `deadline_s`. When it expires the Executor calls `abort()`, stops the robot and the result is `RECOVERABLE`, never silence. An exception raised by a skill stops the robot and becomes `NEEDS_HUMAN`.
- A skill does not call another skill. Composition lives in the plan.

#### v0 skills

| Skill | Parameters | Precondition | Postcondition |
|-------|------------|--------------|---------------|
| `navigate` | `to: str` (location), `via: list[str] = []` (waypoints, in order), `max_speed: float = 0.5` (m/s, 0 < v ≤ 2) | `to` and every `via` location exist, battery ≥ 10 %, robot not in `estop` | robot within `tolerance_m` of `to`. Each leg is a straight line; evidence `reached`, `path`, `distance_m` |
| `detect` | `cls: str`, `expect: int = 1` | robot mode `idle` | at least `expect` objects of class `cls` seen (checked in `execute`, `RECOVERABLE` otherwise); they enter `objects` with their pose; evidence `objects`, `frame_id` |
| `inspect` | `target: str`, `checks: list[str] = []` | `target` is a known location, robot within its tolerance, not in `estop` | evidence contains `frame_id`, a result per check and the list of `anomalies`. `present:<cls>` and `absent:<cls>` are evaluated with the Perceiver; any other check is recorded as not evaluated. An anomaly does not fail the skill |
| `pick` | `object_id: str`, `arm: "left" \| "right" = "right"` | hand empty, object with known pose within 0.9 m of the base | `robot.holding.id == object_id`; the object leaves `objects` |
| `place` | `at: str` (location), `arm = "right"`, `height_m: float = 0.9` | `robot.holding` not null, robot within the tolerance of `at` | `robot.holding is None`; the object is back in `objects` with the pose where it was put down |
| `wait_for_human` | `prompt: str`, `timeout_s: float = 300` (≤ 3600) | – | the robot is stopped and the operator answered `continue`. `abort` or no answer within `timeout_s` gives `RECOVERABLE` |
| `say` | `text: str` (1 to 200 characters) | – | always `SUCCESS`; the text is in a `say` event |

### 3.4 Task Plan

```yaml
# plans/demo_material_runner.yaml (abridged)
id: demo_material_runner
description: "Fetch the red box from shelf A and deliver it to workstation B"
deadline_s: 600
steps:
  - skill: navigate
    params: { to: shelf_A, via: [aisle_in] }
    on_failure: { retry: 2, then: needs_human }
  - skill: detect
    params: { cls: red_box, expect: 1 }
    on_failure: { retry: 2, then: needs_human }
  - skill: pick
    params: { object_id: "$detect.objects[0].id" }   # reference to the evidence of a previous step
    on_failure: { retry: 1, then: needs_human }
  - skill: navigate
    params: { to: workstation_B, via: [aisle_out] }
  - skill: place
    params: { at: workstation_B }
  - skill: say
    params: { text: "Delivery completed" }
```

```python
class OnFailure(BaseModel):
    retry: int = Field(0, ge=0, le=10)
    then: Literal["needs_human", "abort", "skip"] = "needs_human"

class Step(BaseModel):
    skill: str
    params: dict[str, Any] = {}
    on_failure: OnFailure = OnFailure()
    deadline_s: float | None = None     # overrides the skill's default_deadline_s

class TaskPlan(BaseModel):
    id: str                             # [A-Za-z0-9_-]{1,64}
    description: str = ""
    steps: list[Step]                   # at least one
    deadline_s: float | None = None     # time budget of the whole run
```

Rules:
- The plan is validated **before** it starts (`validate_plan`): every `skill` exists in the registry, every `params` passes the skill's schema, every reference points to a previous step. An invalid plan never moves the robot.
- A reference has the form `$<step>.<path>`: `<step>` is either a skill name (the nearest previous step with that skill) or a step index; `<path>` walks the step's evidence with field names and `[i]` indexes, for example `$detect.objects[0].id` or `$2.objects[0].id`. A reference is always the **whole value** of a parameter, never a substring: `"text": "$navigate.reached"` is valid, `"text": "arrived at $navigate.reached"` is not (it is a plain string). References are resolved just before the step runs; a reference that cannot be resolved is a step failure handled by `on_failure.then`.
- The plan is data. It is versioned, diffed, tested.
- No control flow beyond `on_failure`. No `if`, no loops (v0). If they are needed, the Planner generates a different plan.

### 3.5 Robot Adapter

```python
class RobotAdapter(Protocol):
    # Locomotion (high-level: the locomotion controller belongs to the vendor)
    async def walk_to(self, pose: Pose2D, max_speed: float) -> None: ...
    async def stop(self) -> None: ...
    async def get_pose(self) -> Pose2D: ...

    # Manipulation (v0: target positions, no torque)
    async def move_arm(self, arm: Literal["left","right"], target: Pose3D, duration_s: float) -> None: ...
    async def gripper(self, arm: Literal["left","right"], action: Literal["open","close"]) -> None: ...

    # Sensors
    async def get_camera(self, name: str = "head") -> Frame: ...
    async def get_joint_state(self) -> JointState: ...
    async def get_battery(self) -> float: ...

    # Safety
    async def estop(self) -> None: ...        # irreversible until manual reset
    async def heartbeat(self) -> None: ...    # feeds the watchdog; if heartbeats stop, the robot stops

class Frame(BaseModel):                       # an image reference, never the bytes
    id: str; ts: float; camera: str = "head"
    width: int = 0; height: int = 0
    data_ref: str | None = None               # path of the image the adapter wrote, if any
```

Rules:
- Every call has a timeout. An adapter that blocks forever is a bug. *(Not enforced inside the adapters yet: the Executor's per-skill deadline bounds every call made from a skill.)*
- `walk_to` accepts `max_speed` and **cannot** exceed the limit set by the Safety Monitor. The adapter clamps to its `speed_cap`, always, and records `last_applied_speed`.
- After `estop`, every motion command raises until a manual reset (`reset_estop()`, not reachable from the runtime).
- `estop` has no preconditions and cannot fail silently. If the SDK does not respond, the adapter logs it as `FATAL` *(applies to `UnitreeG1Adapter`, not implemented yet)*.
- Three implementations: `FakeAdapter` (in-memory, instantaneous, for unit tests), `SimAdapter` (MuJoCo, kinematic base, ADR-0006), `UnitreeG1Adapter` *(not implemented yet, M4)*.

### 3.6 Perceiver

```python
class Perceiver(Protocol):
    async def detect(self, frame: Frame, cls: str) -> list[ObjectRef]: ...
    async def localize(self, frame: Frame, marker_id: int) -> Pose3D | None: ...
```

Implemented: `FakePerceiver` (configured objects, optional false-negative rate) and `SimPerceiver`, which reads the ground truth from the scene with configurable noise to test the logic without models (section 6). Planned: AprilTags on objects and locations and a fixed-class detector (YOLO on known classes) *(not implemented yet)*; v1: VLM as another `Perceiver` behind the same interface.

### 3.7 Human gateway

```python
OperatorAction = Literal["retry", "skip", "abort", "continue"]

class HumanRequest(BaseModel):
    run_id: str
    step_index: int
    skill: str
    reason: str
    options: list[OperatorAction] = ["retry", "skip", "abort"]

class HumanResponse(BaseModel):
    action: OperatorAction
    note: str = ""

class HumanGateway(Protocol):
    async def ask(self, request: HumanRequest, timeout_s: float) -> HumanResponse: ...
```

Rules:
- The answer is one of the request's `options`, no free-form input. A failed step offers `retry`, `skip`, `abort`; `wait_for_human` offers `continue`, `abort`.
- Implementations: `ScriptedHuman` (fixed answers, for tests, `--operator auto` and `spingi bench`) and the terminal `ConsoleHuman` (ADR-0008).

### 3.8 Events

Every event is a JSONL line. `ts` is wall-clock time; when the adapter has a simulated clock, every event also carries `sim_t` (simulated seconds).

```json
{"ts": 1791073605.255797, "run_id": "r-20261004-022645-bc34e6", "kind": "safety.speed_capped", "sim_t": 0.0, "requested": 1.0, "applied": 0.8}
{"ts": 1791073605.256748, "run_id": "r-20261004-022645-bc34e6", "kind": "step.start", "sim_t": 0.0, "index": 1, "skill": "navigate", "params": {"to": "shelf_A"}}
{"ts": 1791073605.256799, "run_id": "r-20261004-022645-bc34e6", "kind": "skill.start", "sim_t": 0.0, "skill": "navigate", "attempt": 0}
{"ts": 1791073605.271581, "run_id": "r-20261004-022645-bc34e6", "kind": "skill.end", "sim_t": 4.02, "skill": "navigate", "attempt": 0, "outcome": "success", "reason": "", "duration_s": 0.0147}
```

Event kinds (v0):
- run and plan: `run.start`, `run.end`, `plan.validated`, `plan.invalid`;
- steps: `step.start`, `step.end`, `step.retry`, `step.skip`, `step.abort`;
- skills: `skill.start`, `skill.end`, `skill.precondition_failed`, `skill.postcondition_failed`, `skill.deadline`, `skill.exception`, `state.delta`, `perception.result`, `say`, `adapter.call`;
- safety: `safety.armed`, `safety.disarmed`, `safety.speed_capped`, `safety.geofence`, `safety.battery_low`, `safety.estop` (a `FATAL` outcome);
- operator: `human.request`, `human.response`, `human.timeout`, `operator.stop`.

`adapter.error` *(not implemented yet)*.

Besides the events, the episode holds the camera frames taken by skills and the robot and object trajectory (`docs/episode-format.md`). Periodic `WorldState` snapshots *(not implemented yet)*: the state can be rebuilt from the scene and the `state.delta` events. A recorded run is a reproducible **episode** and, in the longer term, a dataset sample for imitation learning.

---

## 4. Execution model

```
errors = validate_plan(plan, registry)
if errors → plan.invalid, run.end(status=invalid_plan); nothing moves

for index, step in plan.steps:
    if plan.deadline_s and elapsed > plan.deadline_s → finish(deadline)
    params = resolve_references(step.params, outputs) → validate against skill.Params
        (failure → escalate(step, reason) according to on_failure.then)

    attempt = 0
    loop:
        check = skill.preconditions(params, state)
        if not check.ok:
            result = RECOVERABLE("precondition: " + check.reason)
        else:
            result = await run_with_deadline(skill.execute, ctx, step.deadline_s or skill.default_deadline_s)
                # deadline  → skill.abort(), robot.stop(), RECOVERABLE
                # exception → robot.stop(), NEEDS_HUMAN
            if result.outcome == SUCCESS:
                candidate = apply_delta(state, result.delta)
                if skill.postconditions(params, candidate).ok:
                    state = candidate; outputs.append(result.evidence); next step
                result = RECOVERABLE("postcondition: " + reason)
        if result.outcome == FATAL: estop(); finish(aborted)
        if result.outcome == RECOVERABLE and attempt < step.on_failure.retry:
            attempt += 1; continue
        decision = escalate(step, result.reason)        # needs_human / abort / skip according to on_failure.then
        retry → attempt = 0; continue
        skip  → outputs.append({}); next step
        abort → finish(aborted)
finish(success)
```

Rules:
- **One skill at a time.** No parallelism between skills in v0. The parallelism that is needed (safety monitor, console) runs in separate `asyncio` tasks that do not move the robot.
- **Precondition idempotence**: a retry re-checks the preconditions. If the object has disappeared in the meantime, we do not retry blindly. A failed precondition counts as a `RECOVERABLE` attempt and consumes the step's retry budget.
- **Retries** apply to `RECOVERABLE` only. `NEEDS_HUMAN` goes straight to escalation.
- **Escalation** follows `on_failure.then`. `skip` and `abort` act without asking (`step.skip`, `step.abort` events). `needs_human` stops the robot, emits `human.request` and waits for the operator with a timeout (300 s by default); no answer means `abort` (`human.timeout`). The operator's answer is one of `retry`, `skip`, `abort`; no other free-form input. `retry` resets the step's retry budget.
- **Time budget per run**: a plan may declare a total `deadline_s` in addition to the per-skill deadline. It is checked before each step on the Executor's clock (wall-clock, monotonic); a step in progress is not interrupted. When it is exhausted the run ends with status `deadline`.
- **Run status**: `success`, `aborted`, `invalid_plan` or `deadline`, in the `run.end` event and in `RunResult`. Every run that does not succeed ends with `robot.stop()`.
- **Operator stop**: Ctrl+C during `spingi run` emits `operator.stop`, e-stops the robot, cancels the run (`run.end` with status `aborted`, reason `operator stop`) and still writes the episode.

---

## 5. Layered safety

| Layer | Where it lives | What it does | Independent of |
|-------|----------------|--------------|----------------|
| S0 | Hardware | Physical e-stop button / vendor remote control | all software |
| S1 | Adapter | Watchdog: if no `heartbeat` arrives within `watchdog_ms`, `stop()`. Implemented in `FakeAdapter` and `SimAdapter` (checked while walking); the heartbeat is sent by the Safety Monitor at every check. Off unless `watchdog_ms` is set: `spingi run` does not set it yet | Executor, Planner |
| S2 | Safety Monitor (separate `asyncio` task) | Geofence (axis-aligned working rectangle) → `stop()`, or `estop()` with `estop_on_geofence`; battery below `min_battery_pct` → `stop()`; speed cap written to the adapter's `speed_cap` when the monitor starts. Checks every 50 ms, or at every cooperative yield in fast simulation. Limits come from the `safety:` section of the scene. Person too close (v1, with depth) *(not implemented yet)* | Executor, Skills |
| S3 | Skill | Preconditions and postconditions; a `FATAL` outcome makes the Executor e-stop the robot | Planner |
| S4 | Planner | Skill whitelist, parameter schema, no access to low-level primitives | – |
| S5 | Operator | Terminal console, Ctrl+C e-stops the robot (`--operator console`). `spingi run` defaults to `--operator auto`, which answers every request with a fixed policy; `spingi plan --run` defaults to the console | – |

Two operating rules:
1. **The LLM is not a safety layer.** It is never asked "is this safe?". The limits are in the runtime.
2. **Every layer has a test that proves it works when the others are broken.** E.g.: a `FakeAdapter` with `watchdog_ms=200` that receives no heartbeat must `stop()` instead of walking (`tests/unit/test_fake_adapter.py`); the geofence stops the robot even though the plan asks for a target outside it (`tests/sim/test_scenarios.py`).

---

## 6. Perception v0

Deliberate choice: a **lightly prepared environment** rather than general perception.

- AprilTags on locations (floor/wall) and on standard containers. Reliable localization, zero training. *(Not implemented yet: `marker_id` is carried in scenes and `ObjectRef`, and `SimPerceiver.localize` answers from the ground truth.)*
- Fixed-class detector for the site's objects (10–20 classes), trained on photos of the site. *(Not implemented yet.)*
- VLM (v1) for `inspect`: "does the display show an error?", "is there a leak?" with a structured answer and confidence. Until then `inspect` evaluates only `present:<cls>` and `absent:<cls>` and records other checks as not evaluated.
- In sim: `SimPerceiver` reads the ground truth from the MuJoCo scene. An object is seen when it is within 3 m and within ±40° of the robot's heading. Noise: a false-negative rate and Gaussian noise on positions (`--noise`, `--sigma`, seeded with `--seed`). It is there to test the retry and escalation logic without depending on a model.
- In unit tests: `FakePerceiver` returns configured objects, optionally with a false-negative rate or always failing.

---

## 7. Planner

### 7.1 StaticPlanner
Loads a `TaskPlan` from YAML (`StaticPlanner(plans_dir).plan(id)` reads `<plans_dir>/<id>.yaml`). It is the default for recurring tasks. Deterministic, testable, no inference cost. The CLI loads the plan file given on the command line in the same way (`load_plan`).

### 7.2 LLMPlanner
- Uses structured output, not tool use (ADR-0010): the answer is constrained by a JSON schema generated from the registry, one `anyOf` variant per skill with that skill's parameter schema, every object strict. Constraints that structured output does not support (lengths, ranges, patterns) are stripped from the schema and enforced afterwards by `validate_plan`.
- The LLM receives: the natural-language request, a one-line summary of each skill, the symbolic world (known locations, known objects, robot position, battery, what it holds) and the `routes:` section of the scene file, which lists waypoints that avoid obstacles.
- Output: a JSON plan → `TaskPlan` → validated like any plan.
- If validation fails, the errors are sent back once; a second failure, a refusal or a truncated answer is a `PlanningError` and nothing runs. The caller (`spingi plan`) reports it.
- A request that cannot be done with the skills, locations and objects of the world is answered with a single `say` step that explains what is missing.
- The Planner **does not see** raw telemetry, camera frames or joints. It sees the symbolic state.
- Default model `claude-opus-5-5`, effort `medium`, server-side fallbacks on; `--model` changes the model.

Example. Request: *"Bring the red box from shelf A to workstation B"* in `warehouse_small` → a plan equivalent to the one in §3.4 without the `say` step. The Planner test is exactly this: given a scene and a request, the produced plan is equivalent to the golden plan. `plans/golden/planner_cases.yaml` holds ten such cases; equivalence means the same skills in the same order and the same parameters with defaults filled in (`say` text, `wait_for_human` prompt and timeout and `on_failure` are not compared; a reference by skill name equals the same reference by index). `spingi eval-planner` runs them and reports the pass rate.

### 7.3 Replanning
v0: no automatic replanning. If the plan fails, escalation. v1: after `needs_human`, the operator can ask the LLMPlanner for an alternative plan starting from the current state *(not implemented yet)*.

---

## 8. Testing strategy

This section is the heart of the spec. Every level must exist before the milestone that requires it.

### 8.1 Pyramid

| Level | What it tests | With what | Where it runs | Target duration |
|-------|---------------|-----------|---------------|-----------------|
| **Unit** (`tests/unit`) | Skills (pre/post), Executor, plan validation, Safety Monitor, metrics and bench, console, episode and schemas, LeRobot export, Planner (with a fake LLM client) | `FakeAdapter`, `FakePerceiver`, `ScriptedHuman`, hand-built `WorldState` | CI, every commit | < 30 s total |
| **Contract** (`tests/contract`) | That every `RobotAdapter` honors the same contract | The same suite parametrized over `FakeAdapter` and `SimAdapter`; `UnitreeG1Adapter` at M4 | CI (Fake, Sim); lab (G1) | < 2 min (sim) |
| **Scenario** (`tests/sim`) | A whole plan in a scene; every golden planner plan runs; Ctrl+C operator stop | Headless MuJoCo, YAML scenes, assertions on the final `WorldState` and on the events | CI, every commit | < 5 min |
| **Golden episode** (`tests/golden`) | That a change does not alter behavior on recorded runs | Three recorded episodes run again with their plan, scene, noise, seed and operator answers; steps, outcomes, retries, operator requests, safety events, final status and final position are compared (`spingi replay`) | CI, every commit | < 1 min |
| **Architecture** (`tests/test_architecture.py`) | Import rules of section 11 | AST scan of `spingi/core` | CI, every commit | < 1 s |
| **Robustness** | Retry and escalation under noise | `spingi bench` with `SimPerceiver` at 20–40 % false negatives and position noise. Injected adapter latency *(not implemented yet in bench)* | on demand (`make bench`); CI nightly *(not set up yet)* | < 20 min |
| **Sim2real gate** | That a skill may move to the robot | Numerical metrics (see 8.4), `spingi bench --gate` | manual, per skill | – |
| **Lab** | Skills on the G1 in a fenced area | Checklist + mandatory recording | lab (M4) | – |

As of 2026-10-04 the runtime suite has 138 tests and runs in about 11 seconds; rendering tests skip themselves without an OpenGL context. No test calls the network.

### 8.2 Concrete examples

```python
# Unit: pick precondition
def test_pick_requires_known_pose_within_reach_and_free_hand():
    skill = PickSkill()
    assert not skill.preconditions(PickParams(object_id="red_box_01"), world()).ok   # unknown object
    no_pose = world(objects={"red_box_01": red_box(with_pose=False)})
    assert not skill.preconditions(PickParams(object_id="red_box_01"), no_pose).ok

# Unit: executor escalation
async def test_precondition_failure_retries_then_escalates(make_executor):
    human = ScriptedHuman(default="abort")
    executor, adapter, log = make_executor(human=human)
    plan = TaskPlan(id="p", steps=[Step(skill="navigate", params={"to": "nowhere"},
                                        on_failure={"retry": 2, "then": "needs_human"})])
    result = await executor.run(plan, world())
    assert result.status == "aborted"
    assert log.count("skill.precondition_failed") == 3     # initial attempt + 2 retries
    assert log.count("human.request") == 1

# Contract: every adapter clamps the speed (FakeAdapter and SimAdapter, built with speed_cap=0.6)
async def test_requested_speed_above_cap_is_clamped(adapter):
    await adapter.walk_to(Pose2D(x=0.5, y=0), max_speed=5.0)
    assert adapter.last_applied_speed <= 0.6

# Scenario
async def test_material_runner_delivers_the_box_in_the_warehouse(tmp_path):
    executor, adapter, log, state = make(WAREHOUSE, sim_perceiver=True, record_dir=tmp_path)
    result = await executor.run(load_plan("plans/demo_material_runner.yaml"), state)
    assert result.ok and result.steps_completed == 8
    box = result.final_state.objects["red_box_01"].pose
    assert 8.1 < box.x < 8.6 and 6.7 < box.y < 7.3     # on the workstation table

# Safety: watchdog
async def test_watchdog_stops_without_heartbeat():
    t = [0.0]
    adapter = FakeAdapter(watchdog_ms=200, clock=lambda: t[0])
    t[0] = 0.5                                          # 500 ms without heartbeat
    await adapter.walk_to(Pose2D(x=5, y=0), max_speed=0.5)
    assert adapter.stop_called == 1 and adapter.pose.x == 0
```

### 8.3 Simulation scenes

Scenes are declarative YAML files. The same file gives the initial `WorldState`, the safety limits, the routes for the planner and, in simulation, the MuJoCo scene (floor, location markers, obstacles with collisions, objects):

```yaml
# sim/scenes/warehouse_small.yaml (abridged)
robot:
  pose: { x: 0.0, y: 0.0, yaw: 0.0 }
  battery_pct: 100
locations:
  dock:          { pose: { x: 0.0, y: 0.0, yaw: 0.0 } }
  aisle_in:      { pose: { x: 4.0, y: 0.0, yaw: 1.57 } }
  shelf_A:       { pose: { x: 4.0, y: 3.0, yaw: 0.0 } }
  workstation_B: { pose: { x: 8.0, y: 7.0, yaw: 0.0 }, tolerance_m: 0.2 }
obstacles:
  - { x: 5.1, y: 3.0, w: 0.5, d: 4.0, h: 1.6 }   # rack A
objects:
  red_box_01: { cls: red_box, marker_id: 7, pose: { x: 4.75, y: 3.0, z: 0.95 } }
safety:
  geofence: { x_min: -1.0, x_max: 10.0, y_min: -1.0, y_max: 9.0 }
  max_speed: 0.8
  min_battery_pct: 5
routes:
  dock->shelf_A: [aisle_in]
```

Perception noise is not part of the scene: it is a run setting (`--noise`, `--sigma`, `--seed`) recorded in the episode manifest so that `spingi replay` can repeat the run.

Rules: one scene per use case (`lab_small`, `lab_blocked`, `lab_geofence`, `warehouse_small` today), several noise settings per scene. Scenes are test data, versioned with the tests.

### 8.4 Sim2real gate (per skill)

A skill moves from sim to the robot only if, over the last 100 runs in sim with noise enabled:

| Metric | Threshold |
|--------|-----------|
| Success rate | ≥ 95 % |
| `needs_human` | ≤ 5 operator requests per 100 runs |
| `FATAL` | 0 runs |
| Safety violations (geofence, low battery) | 0 |
| p95 duration | within the budget declared for the skill *(reported by `spingi bench` as simulated time p50 and p95, not checked yet)* |

And, on the robot, before leaving the lab: the same metrics over ≥ 50 runs in a fenced area.

The gate is implemented in `spingi.metrics.Gate` and checked by `spingi bench` (`--gate` sets the exit code). Today it is applied to a whole plan, which exercises several skills at once; `--runs` defaults to 50, `make bench` uses 100.

### 8.5 Product metrics

- **Task success rate** per task type.
- **Time per task** (p50, p95); in simulation, simulated time.
- **Human interventions per 100 tasks.**
- **Retries per task.**
- **Uptime** (operating hours / planned hours) *(not implemented yet: needs the robot)*.
- **Safety incidents** (any unplanned `estop`).

They are computed from the event log (`spingi.metrics`). No metric requires extra instrumentation: if the log is complete, the metrics come for free (P6).

---

## 9. Observability

- **Event log**: JSONL per run, in `runs/<run_id>/events.jsonl`.
- **Recording**: the run folder is the episode (`docs/episode-format.md`): manifest, copies of plan and scene, events, the trajectory sampled at 10 Hz of simulated time, the camera frames taken by `detect` and `inspect` in `frames/`, and with `--record` a third-person video `run.mp4`. `--zip` packs it.
- **Replay**: `spingi replay <episode_dir>...` runs the episode again with the plan, scene, adapter, noise, seed and operator answers it recorded, and compares the behaviour (section 8.1). The Viewer replays an episode visually in the browser.
- **Dashboard v0**: a page that reads the logs and shows the metrics of §8.5 *(not implemented yet)*. Today `spingi bench` writes the metrics to `runs/bench-<id>/report.json` and `runs.jsonl`.
- **Operator console v0** (ADR-0008): in the terminal, with `--operator console`. One line per meaningful event, a prompt with the offered answers when there is a `human.request` (`retry / skip / abort`, or `continue / abort` for `wait_for_human`), Ctrl+C as the STOP button.

---

## 10. Technology choices

| Choice | Rationale | Alternative discarded |
|--------|-----------|-----------------------|
| Python 3.11+ | Unitree SDK, MuJoCo, ML ecosystem. | C++ in the core: premature. |
| `pydantic` v2 | Validation and JSON for free on every contract. | plain dataclasses: no validation. |
| `asyncio` | Concurrent safety monitor and console without threads. | threads: harder to test. |
| MuJoCo ≥ 3.2 (`sim` extra; 3.14 in `uv.lock`), G1 model from mujoco_menagerie | Fast, headless, runs in CI without a GPU, G1 model available. | Isaac Sim as the only sim: heavy, needs a GPU, not in CI. |
| Isaac Lab (optional) | Only for RL training of policies (locomotion, grasp), outside the runtime. | – |
| `unitree_sdk2_python` | Official SDK for the G1 *(not used yet, M4)*. | ROS2 driver: heavy dependency. |
| Claude API, structured output (`anthropic`, `llm` extra) | Planner with validated structured output (ADR-0010). Model and provider replaceable behind `LLMPlanner`. | Forced tool use: rejected by current Claude models. |
| AprilTag + fixed-class detector *(not implemented yet)* | Robust, zero training for locations. | VLM right away: slow, non-deterministic, hard to test. |
| `pandas` + `pyarrow` (`export` extra) | Parquet files of the LeRobot v3.0 export (ADR-0009). | – |
| `imageio` + `imageio-ffmpeg` (`sim` extra) | Head-camera PNG frames and the run video. | – |
| `trimesh` + `scipy` (`sim` extra) | Only for `scripts/export_g1_glb.py`, which exports the G1 for the Viewer. | – |
| `pytest` + `pytest-asyncio`, `ruff` (`dev` extra) | Standard. | – |
| YAML for plans and scenes | Readable, diffable, not Turing-complete. | Custom DSL: over-engineering. |

Core dependencies: `pydantic`, `pyyaml`. Everything else is in the adapters and in optional extras (`dev`, `sim`, `export`, `llm`).

---

## 11. Repository layout

```
.
├── docs/                      # this spec, episode format, JSON schemas (docs/schemas/)
├── adr/                       # architecture decisions
├── runtime/                   # project 1: Physical Agent Runtime
│   ├── spingi/
│   │   ├── core/              # types, ports, skill + registry, plan, executor, events, ScriptedHuman
│   │   ├── skills/            # one skill per file
│   │   ├── planner/           # StaticPlanner, LLMPlanner, plan schema, golden-case evaluation
│   │   ├── safety/            # SafetyMonitor, SafetyLimits, Geofence
│   │   ├── perception/        # FakePerceiver, SimPerceiver
│   │   ├── adapters/          # fake.py, sim_mujoco/ (unitree_g1/ at M4)
│   │   ├── console/           # terminal operator console
│   │   ├── export/            # LeRobot v3.0 exporter
│   │   └── *.py               # cli, session, bench, metrics, replay, episode, scenes, dotenv
│   ├── plans/                 # TaskPlan YAML; plans/golden/ holds the planner cases
│   ├── sim/scenes/            # YAML scenes
│   ├── sim/models/            # G1 model (mujoco_menagerie)
│   ├── scripts/               # gen_schemas, record_golden, export_g1_glb
│   ├── runs/                  # output (gitignored)
│   ├── tests/                 # unit · contract · sim · golden · test_architecture.py
│   └── pyproject.toml
└── viewer/                    # project 2: web replayer for episodes (see episode-format.md)
```

Rule: `spingi/core` imports nothing from `adapters`, `skills`, `planner`, `perception`, nor ROS; its only third-party imports are `pydantic` and `yaml`. The runtime does not know about the viewer: they talk only through the episode format. The dependency graph is acyclic and is verified by a test (`tests/test_architecture.py`, a simple AST script).

---

## 12. Milestones

| ID | Name | Content | Acceptance criterion (verifiable) | Status |
|----|------|---------|-----------------------------------|--------|
| **M0** | Skeleton | Types, `WorldState`, `Skill`, `TaskPlan`, `FakeAdapter`, Executor, event log, 2 skills (`navigate`, `say`) | A 3-step plan runs on `FakeAdapter`; 100 % unit tests green in CI in < 30 s | Done (2026-10-01). Measured by `test_three_step_plan_runs_on_fake_adapter` and the unit suite in CI |
| **M1** | Sim navigation + inspection | `SimAdapter` MuJoCo with the G1, `warehouse_small` scene, `SimPerceiver`, `inspect` skill, Safety Monitor (watchdog, geofence, speed cap) | "4-point inspection round" scenario: 20/20 runs green in CI; safety tests that prove every layer | Done (2026-10-02). Kinematic base (ADR-0006); the inspection round runs in `lab_small` (`warehouse_small` arrived with M2). Measured by the scenario tests (inspection round, geofence, wall, speed cap), run on every commit; a run without noise is deterministic |
| **M2** | Sim transport | `detect`, `pick`, `place` with markers, `wait_for_human`, console v0, robustness tests with noise | `move_red_box` scenario: ≥ 95 % over 100 runs with noise; correct escalation on failures | Done (2026-10-04). `detect`, `pick`, `place` with a kinematic grasp (ADR-0007, ground-truth perception, no markers yet), `navigate` with `via`, `wait_for_human`, terminal console (ADR-0008), `spingi bench` with the gate, LeRobot export (ADR-0009). Measured with `make bench`: `demo_material_runner` in `warehouse_small`, 100 runs, 20 % false negatives, 0.02 m position noise: 100 % success, 0 operator requests, 0 fatal runs, 0 safety violations. Escalation covered by unit tests and the `blocked_wall_operator` golden episode |
| **M3** | Planner | `StaticPlanner`, `LLMPlanner`, golden plans, replay | 10 natural-language requests → plans equivalent to the golden ones; validation rejects malformed plans | Done (2026-10-04). `LLMPlanner` with structured output (ADR-0010), ten golden cases, `spingi eval-planner`, `spingi replay` with three golden episodes in CI. Measured with `spingi eval-planner` and `claude-opus-5-5`: 10 of 10 equivalent, 9 at the first attempt and 1 after the validation round, 3 to 7 seconds per plan |
| **M4** | Robot in the lab | `UnitreeG1Adapter`, contract tests on the G1, sim2real gate on `navigate` and `inspect` | Gate of §8.4 passed for 2 skills; complete recording of every run; signed lab safety checklist | Not started |
| **M5** | Pilot-ready | `pick`/`place` on the G1 with standard containers, metrics dashboard, operating procedures | Gate passed for 4 skills; 1 day of continuous operation in the lab without `FATAL` | Not started |

M0–M3 do not require the robot. The M3 planner evaluation calls the Claude API and is run on demand, not in CI.

**Status as of 2026-10-04: M0 to M3 complete.** 138 runtime tests green in about 11 seconds. Two clarifications that emerged during implementation are normative and now part of the contracts: a `$step.field` reference is always the whole value of a parameter (§3.4); `on_failure.then: needs_human` stops the robot before asking, and the operator's `retry` answer resets the step's retry budget (§4).

---

## 13. Open questions and ADRs

| # | Question | Decision | ADR | Decide by |
|---|----------|----------|-----|-----------|
| Q1 | ROS2 in the core? | Decided: no (see §2.2) | [0001](../adr/0001-no-ros2-in-core.md) | M0 |
| Q2 | Core stack? | Decided: pure Python, pydantic, asyncio; MuJoCo for CI | [0002](../adr/0002-python-pydantic-asyncio-stack.md) | M0 |
| Q3 | How does the LLM act on the robot? | Decided: it proposes a declarative plan, the runtime executes it | [0003](../adr/0003-llm-proposes-runtime-disposes.md) | M0 |
| Q4 | Plan and scene format? | Decided: YAML data without control flow | [0004](../adr/0004-plans-and-scenes-as-yaml.md) | M0 |
| Q5 | Episode format: our own JSONL or native `LeRobotDataset`? | Decided: our episode with the JSONL event log as source of truth, plus a LeRobot v3.0 exporter | [0005](../adr/0005-jsonl-event-log-as-source-of-truth.md), [0009](../adr/0009-lerobot-export-derived-format.md) | M2 |
| Q6 | G1 model for MuJoCo: official `unitree_mujoco` or MJCF from `mujoco_menagerie`? | Decided: mujoco_menagerie, base moved kinematically | [0006](../adr/0006-kinematic-sim-adapter-with-g1-model.md) | M1 |
| Q7 | Grasp in v0: predefined positions for a standard container or a learned policy? | Decided: predefined, kinematic in simulation | [0007](../adr/0007-predefined-kinematic-grasp.md) | M2 |
| Q8 | Console: CLI/TUI or web? | Decided: terminal console in v0 | [0008](../adr/0008-terminal-operator-console.md) | M2 |
| Q9 | Planner output: tool use or structured output? | Decided: structured output, validated like any plan | [0010](../adr/0010-llm-planner-structured-output.md) | M3 |
| Q10 | Locomotion: vendor controller or our own policy? In simulation, a pre-trained policy or the kinematic base? ("ADR-002" in the first draft of this spec, cited by ADR-0006) | Open. Preferred: vendor controller on the robot; RL policy only if the vendor's is not enough. The simulator moves the base kinematically meanwhile (0006) | – | M4 |
| Q11 | Where the runtime runs ("ADR-006" in the first draft): on-board (Orin) or laptop + network? | Open. Preferred: laptop in the lab; on-board for the pilot | – | M4 |

ADR format: title, context (5 lines), decision (3 lines), consequences (5 lines). One file per ADR in `adr/`, starting from `adr/template.md`.

---

## Appendix A: Glossary

- **Skill**: atomic action with verifiable preconditions and postconditions.
- **TaskPlan**: declarative sequence of skills with a failure policy.
- **WorldState**: symbolic state of the world known to the runtime.
- **Adapter**: translator between the runtime and a robot (real or simulated).
- **Episode / run**: a single, recorded execution of a plan.
- **Sim2real gate**: numerical thresholds that authorize execution on the robot.
- **Supervised mode**: an operator can stop the robot at any time and is required for `human.request`.
