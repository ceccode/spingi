import asyncio

import pytest

from spingi.adapters.fake import FakeAdapter
from spingi.core.human import ScriptedHuman
from spingi.core.plan import Step, TaskPlan
from spingi.core.skill import Skill, SkillResult
from spingi.core.types import Location, Pose2D
from spingi.skills import default_registry
from tests.conftest import world


def three_step_plan() -> TaskPlan:
    return TaskPlan(
        id="three",
        steps=[
            Step(skill="navigate", params={"to": "shelf_A"}),
            Step(skill="say", params={"text": "$navigate.reached"}),
            Step(skill="navigate", params={"to": "workstation_B"}),
        ],
    )


async def test_three_step_plan_runs_on_fake_adapter(make_executor):
    executor, adapter, log = make_executor()
    result = await executor.run(three_step_plan(), world())
    assert result.ok and result.steps_completed == 3
    assert result.final_state.robot.pose == Pose2D(x=6, y=0.5, yaw=0)
    assert log.count("skill.end", outcome="success") == 3
    assert log.find("say")[0].data["text"] == "shelf_A"
    assert log.last.kind == "run.end" and log.last.data["status"] == "success"


async def test_invalid_plan_never_moves_the_robot(make_executor):
    executor, adapter, log = make_executor()
    bad = TaskPlan(id="bad", steps=[Step(skill="navigate", params={"to": "A", "max_speed": 99})])
    result = await executor.run(bad, world())
    assert result.status == "invalid_plan"
    assert not any(name == "walk_to" for name, _ in adapter.calls)
    assert log.count("plan.invalid") == 1


async def test_precondition_failure_retries_then_escalates(make_executor):
    human = ScriptedHuman(default="abort")
    executor, adapter, log = make_executor(human=human)
    plan = TaskPlan(
        id="p", steps=[Step(skill="navigate", params={"to": "nowhere"}, on_failure={"retry": 2, "then": "needs_human"})]
    )
    # 'nowhere' passes static validation (it is a string) but fails the precondition at runtime
    result = await executor.run(plan, world())
    assert result.status == "aborted"
    assert log.count("skill.precondition_failed") == 3  # initial attempt + 2 retries
    assert log.count("human.request") == 1 and len(human.requests) == 1
    assert adapter.stop_called >= 1


async def test_human_can_skip_a_failing_step(make_executor):
    executor, adapter, log = make_executor(human=ScriptedHuman(responses=["skip"]))
    plan = TaskPlan(
        id="p",
        steps=[
            Step(skill="navigate", params={"to": "nowhere"}),
            Step(skill="say", params={"text": "moving on"}),
        ],
    )
    result = await executor.run(plan, world())
    assert result.ok and result.steps_completed == 2
    assert log.count("step.end", outcome="skipped") == 1


async def test_human_retry_resets_the_attempt_budget(make_executor):
    class Flaky(Skill):
        name, Params = "flaky", default_registry().get("say").Params
        calls = 0

        async def execute(self, params, ctx):
            Flaky.calls += 1
            return SkillResult.success() if Flaky.calls >= 3 else SkillResult.recoverable("not yet")

    registry = default_registry()
    registry.register(Flaky())
    executor, adapter, log = make_executor(human=ScriptedHuman(responses=["retry"]))
    executor.registry = registry
    plan = TaskPlan(id="p", steps=[Step(skill="flaky", params={"text": "x"}, on_failure={"retry": 1})])
    result = await executor.run(plan, world())
    assert result.ok and Flaky.calls == 3
    assert log.count("human.request") == 1


async def test_skip_policy_without_human(make_executor):
    human = ScriptedHuman()
    executor, adapter, log = make_executor(human=human)
    plan = TaskPlan(
        id="p",
        steps=[
            Step(skill="navigate", params={"to": "nowhere"}, on_failure={"then": "skip"}),
            Step(skill="say", params={"text": "ok"}),
        ],
    )
    result = await executor.run(plan, world())
    assert result.ok and not human.requests and log.count("step.skip") == 1


async def test_deadline_aborts_skill_and_stops_robot(make_executor):
    adapter = FakeAdapter(walk_delay_s=0.3)
    executor, adapter, log = make_executor(adapter=adapter)
    plan = TaskPlan(
        id="p", steps=[Step(skill="navigate", params={"to": "shelf_A"}, deadline_s=0.05, on_failure={"then": "abort"})]
    )
    result = await executor.run(plan, world())
    assert result.status == "aborted"
    assert log.count("skill.deadline") == 1 and adapter.stop_called >= 1


async def test_fatal_outcome_triggers_estop(make_executor):
    class Boom(Skill):
        name, Params = "boom", default_registry().get("say").Params

        async def execute(self, params, ctx):
            return SkillResult.fatal("collision detected")

    registry = default_registry()
    registry.register(Boom())
    executor, adapter, log = make_executor()
    executor.registry = registry
    plan = TaskPlan(
        id="p", steps=[Step(skill="boom", params={"text": "x"}), Step(skill="say", params={"text": "never"})]
    )
    result = await executor.run(plan, world())
    assert result.status == "aborted" and adapter.estopped
    assert log.count("safety.estop") == 1 and log.count("say") == 0


async def test_exception_in_skill_becomes_needs_human_not_crash(make_executor):
    class Buggy(Skill):
        name, Params = "buggy", default_registry().get("say").Params

        async def execute(self, params, ctx):
            raise RuntimeError("bug")

    registry = default_registry()
    registry.register(Buggy())
    human = ScriptedHuman(default="abort")
    executor, adapter, log = make_executor(human=human)
    executor.registry = registry
    result = await executor.run(TaskPlan(id="p", steps=[Step(skill="buggy", params={"text": "x"})]), world())
    assert result.status == "aborted" and log.count("skill.exception") == 1 and len(human.requests) == 1


async def test_postcondition_failure_is_recoverable(make_executor):
    # The robot "arrives" but the location has an impossible tolerance: the postcondition fails
    state = world()
    state.locations["shelf_A"] = Location(name="shelf_A", pose=Pose2D(x=0, y=2), tolerance_m=-1)
    executor, adapter, log = make_executor()
    plan = TaskPlan(
        id="p", steps=[Step(skill="navigate", params={"to": "shelf_A"}, on_failure={"retry": 1, "then": "abort"})]
    )
    result = await executor.run(plan, state)
    assert result.status == "aborted" and log.count("skill.postcondition_failed") == 2


async def test_human_timeout_aborts(make_executor):
    executor, adapter, log = make_executor(human=ScriptedHuman(never_answers=True), human_timeout_s=0.01)
    plan = TaskPlan(id="p", steps=[Step(skill="navigate", params={"to": "nowhere"})])
    result = await executor.run(plan, world())
    assert result.status == "aborted" and log.count("human.timeout") == 1


async def test_run_deadline_stops_between_steps(make_executor):
    t = [0.0]

    def clock():
        t[0] += 100.0
        return t[0]

    executor, adapter, log = make_executor(clock=clock)
    plan = TaskPlan(
        id="p", deadline_s=50, steps=[Step(skill="say", params={"text": "a"}), Step(skill="say", params={"text": "b"})]
    )
    result = await executor.run(plan, world())
    assert result.status == "deadline" and result.steps_completed <= 1


@pytest.mark.parametrize("n", range(3))
async def test_runs_are_deterministic_on_fake_adapter(make_executor, n):
    executor, adapter, log = make_executor()
    result = await executor.run(three_step_plan(), world())
    assert result.ok
    assert [e.kind for e in log.events].count("skill.start") == 3
    await asyncio.sleep(0)
