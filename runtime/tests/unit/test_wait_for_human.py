import io

from spingi.console import ConsoleHuman
from spingi.core.human import ScriptedHuman
from spingi.core.plan import Step, TaskPlan
from tests.conftest import world


def plan(**on_failure) -> TaskPlan:
    return TaskPlan(
        id="confirm",
        steps=[
            Step(
                skill="wait_for_human",
                params={"prompt": "Load the box, then continue", "timeout_s": 1},
                on_failure=on_failure or {"then": "abort"},
            ),
            Step(skill="say", params={"text": "thanks"}),
        ],
    )


async def test_continue_lets_the_plan_go_on(make_executor):
    human = ScriptedHuman(responses=["continue"])
    executor, adapter, log = make_executor(human=human)
    result = await executor.run(plan(), world())
    assert result.ok and result.outputs[0]["confirmed"] is True
    assert human.requests[0].options == ["continue", "abort"]
    assert adapter.stop_called >= 1  # the robot is held still while waiting


async def test_abort_ends_the_run_with_then_abort(make_executor):
    executor, adapter, log = make_executor(human=ScriptedHuman(responses=["abort"]))
    result = await executor.run(plan(), world())
    assert result.status == "aborted" and log.count("say") == 0


async def test_unattended_default_policy_never_continues(make_executor):
    executor, adapter, log = make_executor(human=ScriptedHuman(default="retry"))  # "retry" is not an option here
    result = await executor.run(plan(), world())
    assert result.status == "aborted"
    assert log.find("human.response")[0].data["action"] == "abort"


async def test_timeout_without_answer(make_executor):
    executor, adapter, log = make_executor(human=ScriptedHuman(never_answers=True))
    result = await executor.run(plan(), world())
    assert result.status == "aborted" and log.count("human.timeout") == 1


async def test_console_offers_only_continue_and_abort():
    out = io.StringIO()
    answers = iter(["r", "c"])
    human = ConsoleHuman(input_fn=lambda prompt: next(answers), out=out)
    from spingi.core.ports import HumanRequest

    req = HumanRequest(
        run_id="r", step_index=-1, skill="wait_for_human", reason="Load the box", options=["continue", "abort"]
    )
    response = await human.ask(req, timeout_s=5)
    assert response.action == "continue" and "please answer one of: c, a" in out.getvalue()


async def test_a_continue_answer_to_an_escalation_is_treated_as_abort(make_executor):
    executor, adapter, log = make_executor(human=ScriptedHuman(responses=["continue"]))
    result = await executor.run(TaskPlan(id="p", steps=[Step(skill="navigate", params={"to": "nowhere"})]), world())
    assert result.status == "aborted"  # an escalation offers retry/skip/abort only
