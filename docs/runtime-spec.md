# Physical Agent Runtime: Specification v0.1

Status: draft · Date: 2026-10-01

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
- Two targets: `SimAdapter` (MuJoCo) and `UnitreeG1Adapter`.
- v0 skills: `navigate`, `detect`, `inspect`, `pick`, `place`, `wait_for_human`, `say`.
- Optional LLM Planner: recurring tasks use static YAML plans.
- Minimal operator console: status, stop, confirm, override.

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
│  LLM tool-use → TaskPlan│◄──────│  parameter schema for each skill   │
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
        └───────────│  marker · detector  │   │  JSONL + frames + state  │
                    │  (VLM in v1)        │   │  → replay, dataset       │
                    └──────────┬──────────┘   └──────────────────────────┘
                               │
                               ▼
                    ┌─────────────────────┐
                    │  RobotAdapter (ABC) │
                    └──┬──────────────┬───┘
                       │              │
             ┌─────────▼───┐   ┌──────▼──────────────┐
             │ SimAdapter  │   │ UnitreeG1Adapter    │
             │ MuJoCo      │   │ unitree_sdk2_python │
             └─────────────┘   └─────────────────────┘
```

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
| **Operator Console** | Shows status, allows stop/confirm/override. | Is not a teleoperation app (v0). |

### 2.2 Why no ROS2 in the core

ROS2 solves problems we do not have in v0: many processes, many languages, many distributed nodes. The cost is high: setup, build system, DDS, difficulty of unit testing. The core stays pure Python with `asyncio`. If a ROS2 node is ever needed (e.g. for Nav2 or for a driver), it lives in an adapter or in a separate process with a narrow interface. This decision is recorded as ADR-001.

---

## 3. Core contracts

All types are `pydantic.BaseModel` (validation and JSON serialization for free). The code below is normative for the signatures and indicative for the implementation.

### 3.1 Base types

```python
from enum import Enum
from pydantic import BaseModel

class Pose2D(BaseModel):
    x: float
    y: float
    yaw: float          # rad

class Pose3D(BaseModel):
    x: float; y: float; z: float
    qx: float; qy: float; qz: float; qw: float

class Location(BaseModel):
    name: str           # "shelf_A", "workstation_3"
    pose: Pose2D
    tolerance_m: float = 0.15

class ObjectRef(BaseModel):
    id: str             # "red_box_01"
    cls: str            # "box"
    pose: Pose3D | None = None
    confidence: float = 0.0
    marker_id: int | None = None   # AprilTag in v0
```

### 3.2 World State

```python
class RobotState(BaseModel):
    pose: Pose2D
    battery_pct: float
    holding: ObjectRef | None = None
    mode: Literal["idle", "walking", "manipulating", "estop"]

class WorldState(BaseModel):
    locations: dict[str, Location]
    objects: dict[str, ObjectRef]
    robot: RobotState
    ts: float
```

Rules:
- The state is updated **only** by the Executor after a skill, or by the Perceiver on request. No skill modifies the state directly: it returns a `StateDelta`.
- It is JSON-serializable at any time. The simplest test in the world: `WorldState.model_validate(state.model_dump())`.

### 3.3 Skill

```python
class SkillOutcome(str, Enum):
    SUCCESS = "success"
    RECOVERABLE = "recoverable"   # retry, possibly with different parameters
    NEEDS_HUMAN = "needs_human"   # stop and ask
    FATAL = "fatal"               # stop everything, do not retry

class SkillResult(BaseModel):
    outcome: SkillOutcome
    delta: StateDelta | None = None
    reason: str = ""
    evidence: dict = {}           # e.g. {"frame_id": "...", "detections": [...]}

class Check(BaseModel):
    ok: bool
    reason: str = ""

class SkillContext(BaseModel):
    state: WorldState
    robot: "RobotAdapter"
    perceiver: "Perceiver"
    log: "EventLog"
    deadline_s: float

class Skill(Protocol):
    name: str
    params_schema: type[BaseModel]

    def preconditions(self, params, state: WorldState) -> Check: ...
    async def execute(self, params, ctx: SkillContext) -> SkillResult: ...
    def postconditions(self, params, state: WorldState) -> Check: ...
    async def abort(self) -> None: ...
```

Rules:
- `preconditions` and `postconditions` are **pure**: they read the state and do not touch the robot. Testable with a hand-built `WorldState`.
- `execute` is the only place where `RobotAdapter` and `Perceiver` are called.
- Every skill has a `deadline_s`. When it expires the Executor calls `abort()` and the result is `RECOVERABLE` or `NEEDS_HUMAN`, never silence.
- A skill does not call another skill. Composition lives in the plan.

#### v0 skills

| Skill | Parameters | Precondition | Postcondition |
|-------|------------|--------------|---------------|
| `navigate` | `to: str` (location) | location exists, battery > threshold, robot not in `estop` | robot within `tolerance_m` of `to` |
| `detect` | `cls: str`, `expect: int = 1` | robot stopped | `objects` contains at least `expect` objects of class `cls` with a pose |
| `inspect` | `target: str`, `checks: list[str]` | robot at a location | `evidence` contains a frame + outcome for each check |
| `pick` | `object_id: str` | object with known pose within arm reach, hand empty | `robot.holding == object_id` |
| `place` | `at: str` (location or surface) | `robot.holding` not null, robot at `at` | `robot.holding is None`, object at `at` |
| `wait_for_human` | `prompt: str`, `timeout_s: int` | – | operator confirmed or timeout |
| `say` | `text: str` | – | always `SUCCESS` |

### 3.4 Task Plan

```yaml
# plans/move_red_box.yaml
id: move_red_box
description: "Bring the red box from workstation A to B"
steps:
  - skill: navigate
    params: { to: shelf_A }
  - skill: detect
    params: { cls: red_box, expect: 1 }
    on_failure: { retry: 2, then: needs_human }
  - skill: pick
    params: { object_id: "$detect.objects[0].id" }   # reference to the previous step's output
    on_failure: { retry: 1, then: needs_human }
  - skill: navigate
    params: { to: workstation_B }
  - skill: place
    params: { at: workstation_B }
  - skill: say
    params: { text: "Delivery completed" }
```

```python
class OnFailure(BaseModel):
    retry: int = 0
    then: Literal["needs_human", "abort", "skip"] = "needs_human"

class Step(BaseModel):
    skill: str
    params: dict
    on_failure: OnFailure = OnFailure()

class TaskPlan(BaseModel):
    id: str
    description: str
    steps: list[Step]
```

Rules:
- The plan is validated **before** it starts: every `skill` exists in the registry, every `params` passes the skill's schema, every `$step.field` reference points to a previous step.
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
    async def heartbeat(self) -> None: ...    # called by the watchdog; if it stops, the robot stops
```

Rules:
- Every call has a timeout. An adapter that blocks forever is a bug.
- `walk_to` accepts `max_speed` and **cannot** exceed the limit set by the Safety Monitor. The adapter clamps, always.
- `estop` has no preconditions and cannot fail silently. If the SDK does not respond, the adapter logs it as `FATAL`.
- Three implementations: `FakeAdapter` (in-memory, instantaneous, for unit tests), `SimAdapter` (MuJoCo), `UnitreeG1Adapter`.

### 3.6 Perceiver

```python
class Perceiver(Protocol):
    async def detect(self, frame: Frame, cls: str) -> list[ObjectRef]: ...
    async def localize(self, frame: Frame, marker_id: int) -> Pose3D | None: ...
```

v0: AprilTags on objects and locations + fixed-class detector (YOLO on known classes). v1: VLM as a second `Perceiver` behind the same interface. In simulation, `SimPerceiver` reads the ground truth from the scene, with configurable noise, to test the logic without models.

### 3.7 Events

Every event is a JSONL line:

```json
{"ts": 1759300000.123, "run_id": "r-2026-10-01-0007", "kind": "skill.start", "skill": "navigate", "params": {"to": "shelf_A"}, "state_hash": "ab12"}
{"ts": 1759300004.871, "run_id": "r-2026-10-01-0007", "kind": "skill.end",   "skill": "navigate", "outcome": "success", "duration_s": 4.7}
{"ts": 1759300005.002, "run_id": "r-2026-10-01-0007", "kind": "safety.speed_capped", "requested": 1.2, "applied": 0.6}
```

Event kinds (v0): `run.start`, `run.end`, `plan.validated`, `step.start`, `step.end`, `skill.start`, `skill.end`, `state.delta`, `perception.result`, `safety.*`, `human.request`, `human.response`, `adapter.call`, `adapter.error`.

Besides the events, the Recorder saves camera frames and `WorldState` snapshots at intervals. A recorded run is a reproducible **episode** and, in the longer term, a dataset sample for imitation learning.

---

## 4. Execution model

```
for step in plan.steps:
    skill = registry[step.skill]
    check = skill.preconditions(step.params, state)
    if not check.ok → escalate(step, check.reason)

    attempt = 0
    while True:
        result = await run_with_deadline(skill.execute, ctx)
        log(result)
        if result.outcome == SUCCESS:
            state = apply(state, result.delta)
            post = skill.postconditions(step.params, state)
            if post.ok: break
            result = RECOVERABLE(post.reason)
        if result.outcome == RECOVERABLE and attempt < step.on_failure.retry:
            attempt += 1; continue
        if result.outcome == FATAL: estop(); abort_run()
        escalate(step, result.reason)   # needs_human / abort / skip according to on_failure
```

Rules:
- **One skill at a time.** No parallelism between skills in v0. The parallelism that is needed (watchdog, telemetry, console) runs in separate `asyncio` tasks that do not move the robot.
- **Precondition idempotence**: a retry re-checks the preconditions. If the object has disappeared in the meantime, we do not retry blindly.
- **Escalation** = `human.request` event + robot stopped + wait with timeout. The operator's answer is one of `retry`, `skip`, `abort`. No other free-form input.
- **Time budget per run**: a plan has a total `deadline_s` in addition to the per-skill deadline.

---

## 5. Layered safety

| Layer | Where it lives | What it does | Independent of |
|-------|----------------|--------------|----------------|
| S0 | Hardware | Physical e-stop button / vendor remote control | all software |
| S1 | Adapter | Watchdog: if the Executor does not send `heartbeat` within N ms, `stop()` | Executor, Planner |
| S2 | Safety Monitor (separate process/task) | Geofence (working polygon), speed cap, minimum battery, person too close (v1, with depth) → `stop()` or `estop()` | Executor, Skills |
| S3 | Skill | Preconditions and postconditions | Planner |
| S4 | Planner | Skill whitelist, parameter schema, no access to low-level primitives | – |
| S5 | Operator | Console with an always-visible stop; supervised mode by default | – |

Two operating rules:
1. **The LLM is not a safety layer.** It is never asked "is this safe?". The limits are in the runtime.
2. **Every layer has a test that proves it works when the others are broken.** E.g.: a `FakeExecutor` that stops sending heartbeats must produce `stop()` within N ms.

---

## 6. Perception v0

Deliberate choice: a **lightly prepared environment** rather than general perception.

- AprilTags on locations (floor/wall) and on standard containers. Reliable localization, zero training.
- Fixed-class detector for the site's objects (10–20 classes), trained on photos of the site.
- VLM (v1) for `inspect`: "does the display show an error?", "is there a leak?" with a structured answer and confidence.
- In sim: `SimPerceiver` reads the ground truth with Gaussian noise and a configurable false negative rate. It is there to test the retry and escalation logic without depending on a model.

---

## 7. Planner

### 7.1 StaticPlanner
Loads a `TaskPlan` from YAML. It is the default for recurring tasks. Deterministic, testable, no inference cost.

### 7.2 LLMPlanner
- Uses tool-use: every skill in the registry is exposed as a tool with its `params_schema`.
- The LLM receives: the natural-language request, the `WorldState` (known locations and objects, battery), the list of skills.
- Output: a list of tool calls → converted into a `TaskPlan` → validated like any plan.
- If validation fails, it retries once with the error; then `needs_human`.
- The Planner **does not see** raw telemetry, camera frames or joints. It sees the symbolic state.

Example. Request: *"bring the red box from workstation A to B"* → a plan identical to the one in §3.4. The Planner test is exactly this: given a state and a request, the produced plan is equivalent (same sequence of skills and parameters) to the golden plan.

### 7.3 Replanning
v0: no automatic replanning. If the plan fails, escalation. v1: after `needs_human`, the operator can ask the LLMPlanner for an alternative plan starting from the current state.

---

## 8. Testing strategy

This section is the heart of the spec. Every level must exist before the milestone that requires it.

### 8.1 Pyramid

| Level | What it tests | With what | Where it runs | Target duration |
|-------|---------------|-----------|---------------|-----------------|
| **Unit** | Skills (pre/post), Executor, plan validation, Safety Monitor, Planner (with a mocked LLM) | `FakeAdapter`, `FakePerceiver`, hand-built `WorldState` | CI, every commit | < 30 s total |
| **Contract** | That every `RobotAdapter` honors the same contract | The same suite parametrized over `FakeAdapter`, `SimAdapter`, `UnitreeG1Adapter` | CI (Fake, Sim); lab (G1) | < 2 min (sim) |
| **Scenario** | A whole plan in a scene | Headless MuJoCo, YAML scenes, assertions on the final `WorldState` | CI, every commit | < 5 min |
| **Golden episode** | That a change does not alter behavior on recorded runs | Replay of the event log with the adapter in replay mode | CI | < 1 min |
| **Robustness** | Retry and escalation under noise | `SimPerceiver` with 20–40 % false negatives, injected adapter latency | CI nightly | < 20 min |
| **Sim2real gate** | That a skill may move to the robot | Numerical metrics (see 8.4) | manual, per skill | – |
| **Lab** | Skills on the G1 in a fenced area | Checklist + mandatory recording | lab | – |

### 8.2 Concrete examples

```python
# Unit: pick precondition
def test_pick_requires_known_pose():
    state = world(objects={"box1": ObjectRef(id="box1", cls="box", pose=None)})
    assert not PickSkill().preconditions({"object_id": "box1"}, state).ok

# Unit: executor escalation
async def test_executor_escalates_after_retries():
    adapter = FakeAdapter(); perceiver = FakePerceiver(always_fail=True)
    run = await Executor(...).run(plan_with(retry=2))
    assert run.events.count("skill.start", skill="detect") == 3
    assert run.events.last.kind == "human.request"

# Contract: every adapter clamps the speed
@pytest.mark.parametrize("adapter", [FakeAdapter(), SimAdapter(scene="empty")])
async def test_speed_is_capped(adapter):
    monitor = SafetyMonitor(max_speed=0.5)
    await adapter.walk_to(Pose2D(1, 0, 0), max_speed=2.0)
    assert adapter.last_applied_speed <= 0.5

# Scenario
async def test_move_red_box_scene():
    sim = SimAdapter(scene="sim/scenes/warehouse_small.yaml")
    run = await Executor(sim, SimPerceiver(sim)).run(load_plan("move_red_box"))
    assert run.ok
    assert sim.world.objects["red_box_01"].near("workstation_B", tol=0.2)

# Safety: watchdog
async def test_watchdog_stops_without_heartbeat():
    adapter = FakeAdapter(watchdog_ms=200)
    await adapter.walk_to(Pose2D(5, 0, 0), max_speed=0.5)
    await asyncio.sleep(0.4)       # no heartbeat
    assert adapter.state.mode == "idle" and adapter.stop_called
```

### 8.3 Simulation scenes

Scenes are declarative YAML files that generate the MuJoCo scene:

```yaml
# sim/scenes/warehouse_small.yaml
robot: g1
locations:
  shelf_A:       { x: 0.0, y: 2.0, yaw: 1.57 }
  workstation_B: { x: 6.0, y: 0.5, yaw: 0.0 }
objects:
  red_box_01: { cls: red_box, at: shelf_A, marker_id: 7 }
obstacles:
  - { type: box, x: 3.0, y: 1.0, w: 0.6, d: 0.6, h: 1.2 }
noise:
  perceiver_false_negative: 0.1
  pose_sigma_m: 0.03
```

Rules: one scene per use case, several variants with noise. Scenes are test data, versioned with the tests.

### 8.4 Sim2real gate (per skill)

A skill moves from sim to the robot only if, over the last 100 runs in sim with noise enabled:

| Metric | Threshold |
|--------|-----------|
| Success rate | ≥ 95 % |
| `needs_human` | ≤ 5 % |
| `FATAL` | 0 |
| Safety violations (speed cap, geofence) | 0 |
| p95 duration | within the budget declared for the skill |

And, on the robot, before leaving the lab: the same metrics over ≥ 50 runs in a fenced area.

### 8.5 Product metrics

- **Task success rate** per task type.
- **Time per task** (p50, p95).
- **Human interventions per 100 tasks.**
- **Uptime** (operating hours / planned hours).
- **Safety incidents** (any unplanned `estop`).

They are computed from the event log. No metric requires extra instrumentation: if the log is complete, the metrics come for free (P6).

---

## 9. Observability

- **Event log**: JSONL per run, in `runs/<run_id>/events.jsonl`.
- **Recording**: camera frames (subsampled) and `WorldState` snapshots in `runs/<run_id>/`.
- **Replay**: `spingi replay <run_id>` rebuilds the state step by step and, in sim, replays the run.
- **Dashboard v0**: a page that reads the logs and shows the metrics of §8.5. Nothing more.
- **Operator console v0**: current status, last event, STOP button, `retry / skip / abort` buttons when there is a `human.request`.

---

## 10. Technology choices

| Choice | Rationale | Alternative discarded |
|--------|-----------|-----------------------|
| Python 3.11+ | Unitree SDK, MuJoCo, ML ecosystem. | C++ in the core: premature. |
| `pydantic` | Validation and JSON for free on every contract. | plain dataclasses: no validation. |
| `asyncio` | Concurrent watchdog, telemetry and console without threads. | threads: harder to test. |
| MuJoCo | Fast, headless, runs in CI without a GPU, G1 model available. | Isaac Sim as the only sim: heavy, needs a GPU, not in CI. |
| Isaac Lab (optional) | Only for RL training of policies (locomotion, grasp), outside the runtime. | – |
| `unitree_sdk2_python` | Official SDK for the G1. | ROS2 driver: heavy dependency. |
| Claude API (tool use) | Planner with validated structured output. Model and provider replaceable behind `LLMPlanner`. | – |
| AprilTag + fixed-class detector | Robust, zero training for locations. | VLM right away: slow, non-deterministic, hard to test. |
| `pytest` + `pytest-asyncio` | Standard. | – |
| YAML for plans and scenes | Readable, diffable, not Turing-complete. | Custom DSL: over-engineering. |

Core dependencies: `pydantic`, `numpy`. Everything else in the adapters and in optional extras.

---

## 11. Repository layout

```
spingi/
├── docs/                      # this spec, episode format, JSON schemas
├── adr/                       # architecture decisions
├── runtime/                   # project 1: Physical Agent Runtime
│   ├── spingi/
│   │   ├── core/              # types, ports, WorldState, Executor, registry, events
│   │   ├── skills/            # one skill per file
│   │   ├── planner/           # StaticPlanner, LLMPlanner
│   │   ├── safety/            # SafetyMonitor, watchdog (M1)
│   │   ├── perception/        # Perceiver, AprilTag, detector, FakePerceiver
│   │   ├── adapters/          # fake.py, sim_mujoco/, unitree_g1/
│   │   └── console/           # minimal operator console (M2)
│   ├── plans/                 # TaskPlan YAML
│   ├── sim/scenes/            # YAML scenes
│   ├── sim/models/            # G1 model
│   ├── runs/                  # output (gitignored)
│   ├── tests/                 # unit · contract · sim · golden · architecture
│   └── pyproject.toml
└── viewer/                    # project 2: web replayer for episodes (see episode-format.md)
```

Rule: `spingi/core` imports nothing from `adapters`, `perception`, `planner`. The runtime does not know about the viewer: they talk only through the episode format. The dependency graph is acyclic and is verified by a test (`import-linter` or a simple script).

---

## 12. Milestones

| ID | Name | Content | Acceptance criterion (verifiable) |
|----|------|---------|-----------------------------------|
| **M0** | Skeleton | Types, `WorldState`, `Skill`, `TaskPlan`, `FakeAdapter`, Executor, event log, 2 skills (`navigate`, `say`) | A 3-step plan runs on `FakeAdapter`; 100 % unit tests green in CI in < 30 s |
| **M1** | Sim navigation + inspection | `SimAdapter` MuJoCo with the G1, `warehouse_small` scene, `SimPerceiver`, `inspect` skill, Safety Monitor (watchdog, geofence, speed cap) | "4-point inspection round" scenario: 20/20 runs green in CI; safety tests that prove every layer |
| **M2** | Sim transport | `detect`, `pick`, `place` with markers, `wait_for_human`, console v0, robustness tests with noise | `move_red_box` scenario: ≥ 95 % over 100 runs with noise; correct escalation on failures |
| **M3** | Planner | `StaticPlanner`, `LLMPlanner`, golden plans, replay | 10 natural-language requests → plans equivalent to the golden ones; validation rejects malformed plans |
| **M4** | Robot in the lab | `UnitreeG1Adapter`, contract tests on the G1, sim2real gate on `navigate` and `inspect` | Gate of §8.4 passed for 2 skills; complete recording of every run; signed lab safety checklist |
| **M5** | Pilot-ready | `pick`/`place` on the G1 with standard containers, metrics dashboard, operating procedures | Gate passed for 4 skills; 1 day of continuous operation in the lab without `FATAL` |

M0–M3 do not require the robot.

**Status as of 2026-10-04: M0, M1 and M2 complete.** `SimAdapter` for MuJoCo with the G1 (ADR-0006), `SimPerceiver` with configurable noise, independent `SafetyMonitor`, skills `navigate` (with waypoints), `detect`, `pick`, `place` (kinematic grasp, ADR-0007), `inspect`, `wait_for_human`, `say`; terminal operator console with Ctrl+C as the stop button (ADR-0008); `spingi bench` computes the metrics of section 8.5 from the event log and checks the gate of section 8.4; `spingi export lerobot` writes LeRobotDataset v3.0 (ADR-0009). M2 acceptance: the material runner in `warehouse_small` passed the gate over 100 simulated runs with 20 % perception false negatives and 2 cm position noise (100 % success, 0 operator requests, 0.25 retries per run). 108 tests green in CI in under 10 seconds. Two clarifications that emerged during implementation are now normative:
- A `$step.field` reference is always the **whole value** of a parameter, never a substring. `"text": "$navigate.reached"` is valid; `"text": "arrived at $navigate.reached"` is not.
- `on_failure.then: needs_human` stops the robot before asking; the operator's `retry` answer resets the step's retry budget.

---

## 13. Open questions and ADRs to write

| ID | Question | Preferred option | Decide by |
|----|----------|------------------|-----------|
| ADR-001 | ROS2 in the core? | No (see §2.2) | M0 |
| ADR-002 | Locomotion: vendor controller or our own policy? | Vendor in v0; RL policy only if the vendor's is not enough | M1 |
| ADR-003 | G1 model for MuJoCo: official `unitree_mujoco` or MJCF from `mujoco_menagerie`? | Decided: mujoco_menagerie (adr/0006) | M1 |
| ADR-004 | Grasp in v0: predefined positions for a standard container or a learned policy? | Decided: predefined, kinematic in simulation (adr/0007) | M2 |
| ADR-005 | Episode format: our own JSONL or native `LeRobotDataset`? | Decided: our episode + LeRobot v3.0 exporter (adr/0009) | M2 |
| ADR-006 | Where the runtime runs: on-board (Orin) or laptop + network? | Laptop in the lab; on-board for the pilot | M4 |
| ADR-007 | Console: CLI/TUI or web? | Decided: terminal console in v0 (adr/0008) | M2 |

ADR format: title, context (5 lines), decision (3 lines), consequences (5 lines). One file per ADR in `adr/`.

---

## Appendix A: Glossary

- **Skill**: atomic action with verifiable preconditions and postconditions.
- **TaskPlan**: declarative sequence of skills with a failure policy.
- **WorldState**: symbolic state of the world known to the runtime.
- **Adapter**: translator between the runtime and a robot (real or simulated).
- **Episode / run**: a single, recorded execution of a plan.
- **Sim2real gate**: numerical thresholds that authorize execution on the robot.
- **Supervised mode**: an operator can stop the robot at any time and is required for `human.request`.
