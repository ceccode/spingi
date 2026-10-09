"""A plan may only use the skills the robot can run (ADR-0012): checked before anything moves, and the planner
never even sees the others."""

from pathlib import Path

import pytest

from spingi.adapters.fake import FakeAdapter
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.human import ScriptedHuman
from spingi.core.plan import Step, TaskPlan, validate_plan
from spingi.core.ports import CapabilityMissing
from spingi.perception.fake import FakePerceiver
from spingi.planner.llm import LLMPlanner
from spingi.planner.schema import plan_schema
from spingi.robots import GO2
from spingi.scenes import load_world
from spingi.session import SessionConfig, run_session
from spingi.skills import default_registry

LAB = Path("sim/scenes/lab_small.yaml")
WAREHOUSE = Path("sim/scenes/warehouse_small.yaml")
PICK_PLAN = TaskPlan(
    id="fetch",
    steps=[
        Step(skill="navigate", params={"to": "shelf_A"}),
        Step(skill="detect", params={"cls": "red_box"}),
        Step(skill="pick", params={"object_id": "$detect.objects[0].id"}),
    ],
)


def test_the_registry_subset_for_a_quadruped_has_no_arm_skills():
    names = default_registry().subset(GO2.capabilities).names()
    assert names == ["detect", "inspect", "navigate", "say", "wait_for_human"]
    assert default_registry().subset(frozenset()).names() == ["say", "wait_for_human"]


def test_validate_plan_names_the_missing_capability():
    errors = validate_plan(PICK_PLAN, default_registry(), GO2.capabilities)
    assert errors == ["step 2: skill 'pick' needs arm, which this robot does not have"]
    assert validate_plan(PICK_PLAN, default_registry()) == []  # without a robot, only the schema is checked


async def test_a_pick_plan_is_invalid_on_a_robot_without_an_arm_before_it_moves():
    robot = FakeAdapter(capabilities=GO2.capabilities)
    log = EventLog(run_id="r")
    executor = Executor(default_registry(), robot, FakePerceiver(), ScriptedHuman(default="abort"), log=log)
    result = await executor.run(PICK_PLAN, load_world(WAREHOUSE))
    assert result.status == "invalid_plan" and "needs arm" in result.reason
    assert [c[0] for c in robot.calls] == []  # not even the navigate step ran


async def test_an_adapter_without_an_arm_refuses_arm_commands_instead_of_pretending():
    robot = FakeAdapter(capabilities=GO2.capabilities)
    with pytest.raises(CapabilityMissing):
        await robot.gripper("right", "close")
    assert not robot.gripper_closed


def test_the_planner_for_a_quadruped_cannot_name_pick_and_says_which_robot_it_is():
    registry = default_registry().subset(GO2.capabilities)
    planner = LLMPlanner(registry, client=object(), robot=GO2.label)
    variants = plan_schema(registry)["properties"]["steps"]["items"]["anyOf"]
    assert {v["properties"]["skill"]["const"] for v in variants} == set(registry.names())
    message = planner.first_message("Patrol the lab", load_world(LAB), None)
    assert message.startswith(f"Robot: {GO2.label}\n") and "- pick" not in message


async def test_a_session_records_which_robot_ran(tmp_path):
    cfg = SessionConfig(plan=Path("plans/demo_inspection_round.yaml"), scene=LAB, robot="go2", runs_dir=tmp_path)
    result = await run_session(cfg, ScriptedHuman(default="abort"))
    assert result.ok
    manifest = (result.run_dir / "manifest.json").read_text()
    assert '"model": "unitree_go2"' in manifest and '"adapter": "fake"' in manifest


async def test_episodes_of_different_robots_do_not_share_a_lerobot_dataset(tmp_path):
    pytest.importorskip("pandas")
    from spingi.export.lerobot import export_lerobot

    plan = Path("plans/demo_inspection_round.yaml")
    runs = []
    for robot in ("g1", "go2"):
        cfg = SessionConfig(plan=plan, scene=LAB, robot=robot, runs_dir=tmp_path / robot)
        runs.append((await run_session(cfg, ScriptedHuman(default="abort"))).run_dir)
    with pytest.raises(ValueError, match="different robots"):
        export_lerobot(runs, tmp_path / "dataset")
    info = export_lerobot(runs[1:], tmp_path / "dataset")
    assert info["robot_type"] == "unitree_go2"
