import asyncio
import io

import pytest

from spingi.console import ConsoleHuman, ConsoleReporter
from spingi.console.terminal import describe
from spingi.core.events import Event, EventLog
from spingi.core.plan import Step, TaskPlan
from spingi.core.ports import HumanRequest
from tests.conftest import world


def request() -> HumanRequest:
    return HumanRequest(run_id="r", step_index=2, skill="pick", reason="grasp failed")


async def test_console_human_reprompts_until_a_valid_answer():
    answers = iter(["maybe", "", "S"])
    out = io.StringIO()
    human = ConsoleHuman(input_fn=lambda prompt: next(answers), out=out)
    response = await human.ask(request(), timeout_s=5)
    assert response.action == "skip" and response.note == "console"
    text = out.getvalue()
    assert "OPERATOR NEEDED" in text and "grasp failed" in text and text.count("please answer") == 2


async def test_console_human_times_out():
    def slow(prompt: str) -> str:
        import time

        time.sleep(0.5)
        return "r"

    with pytest.raises(TimeoutError):
        await ConsoleHuman(input_fn=slow, out=io.StringIO()).ask(request(), timeout_s=0.05)


async def test_console_drives_an_executor_escalation(make_executor):
    human = ConsoleHuman(input_fn=lambda prompt: "a", out=io.StringIO())
    executor, adapter, log = make_executor(human=human)
    plan = TaskPlan(id="p", steps=[Step(skill="navigate", params={"to": "nowhere"})])
    result = await executor.run(plan, world())
    assert result.status == "aborted" and len(human.requests) == 1
    assert log.find("human.response")[0].data["action"] == "abort"


def test_reporter_prints_meaningful_events_only():
    out = io.StringIO()
    log = EventLog(run_id="r-1", sim_clock=lambda: 3.25)
    log.subscribe(ConsoleReporter(out=out))
    log.emit("run.start", plan_id="p", steps=2)
    log.emit("skill.start", skill="navigate", attempt=0)  # not printed
    log.emit("step.start", index=0, skill="navigate", params={"to": "shelf_A"})
    log.emit("skill.end", skill="detect", outcome="recoverable", reason="found 0")
    log.emit("perception.result", cls="red_box", found=["red_box_01"])
    log.emit("safety.geofence", pose={"x": 9.0, "y": 0.0})
    log.emit("run.end", status="success", steps_completed=2)
    lines = out.getvalue().splitlines()
    assert len(lines) == 6 and all(line.startswith("[    3.2s]") or line.startswith("[    3.3s]") for line in lines)
    assert "step 0  navigate" in lines[1] and "recoverable" in lines[2] and "red_box_01" in lines[3]
    assert "SAFETY geofence" in lines[4] and "run end: success" in lines[5]


def test_describe_ignores_unknown_events():
    assert describe(Event(ts=0, run_id="r", kind="adapter.call", data={})) is None


async def test_subscriber_sees_events_in_order():
    seen: list[str] = []
    log = EventLog(run_id="r")
    log.subscribe(lambda e: seen.append(e.kind))
    log.emit("a")
    log.emit("b")
    await asyncio.sleep(0)
    assert seen == ["a", "b"]
