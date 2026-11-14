"""A fall ends the run like an e-stop, says where it happened, and counts in the gate (ADR-0015)."""

from pathlib import Path

import pytest

from spingi.adapters.fake import FakeAdapter
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.human import ScriptedHuman
from spingi.core.plan import Step, TaskPlan
from spingi.core.ports import RobotFell
from spingi.metrics import run_metrics, summarize
from spingi.perception.fake import FakePerceiver
from spingi.scenes import load_world
from spingi.session import SessionConfig
from spingi.skills import default_registry

LAB = Path("sim/scenes/lab_small.yaml")


class FallingAdapter(FakeAdapter):
    async def walk_to(self, pose, max_speed):
        self.estopped = True
        raise RobotFell("the robot fell at (1.20, 0.30)")


async def test_a_fall_is_fatal_recorded_and_counted():
    robot = FallingAdapter()
    log = EventLog(run_id="r-fall")
    executor = Executor(default_registry(), robot, FakePerceiver(), ScriptedHuman(default="retry"), log=log)
    plan = TaskPlan(id="p", steps=[Step(skill="navigate", params={"to": "shelf_A"})])
    result = await executor.run(plan, load_world(LAB))
    assert result.status == "estop" and "fell at (1.20, 0.30)" in result.reason
    assert [e.data["reason"] for e in log.find("robot.fell")] == ["the robot fell at (1.20, 0.30)"]
    assert not log.find("human.request")  # a fallen robot is not a retry: nobody is asked
    metrics = run_metrics(log.events, 1)
    assert metrics.fatal and metrics.fell
    report = summarize([metrics], plan_id="p", scene="s", adapter="sim", perception_noise=0, position_sigma_m=0)
    assert report.falls == 1 and not report.passed


def test_physics_locomotion_needs_the_simulator(tmp_path):
    plan = Path("plans/demo_inspection_round.yaml")
    with pytest.raises(ValueError, match="sim adapter"):
        SessionConfig(plan=plan, scene=LAB, locomotion="physics", runs_dir=tmp_path)
