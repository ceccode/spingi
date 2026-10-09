"""The Go2 quadruped in MuJoCo: the same kinematic adapter, a different model and no arm (ADR-0012)."""

from pathlib import Path

import mujoco
import pytest

from spingi.adapters.sim_mujoco import SimAdapter
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.human import ScriptedHuman
from spingi.core.plan import Step, TaskPlan, load_plan
from spingi.core.ports import CapabilityMissing
from spingi.core.types import Pose2D, Pose3D
from spingi.perception.sim import SimPerceiver
from spingi.robots import GO2
from spingi.scenes import load_world
from spingi.skills import default_registry

LAB = Path("sim/scenes/lab_small.yaml")
BLOCKED = Path("sim/scenes/lab_blocked.yaml")
WAREHOUSE = Path("sim/scenes/warehouse_small.yaml")


def make(scene: Path, **kw):
    adapter = SimAdapter(scene, robot="go2", **kw)
    log = EventLog(run_id="r-go2")
    executor = Executor(
        registry=default_registry(),
        robot=adapter,
        perceiver=SimPerceiver(adapter),
        human=ScriptedHuman(default="abort"),
        log=log,
    )
    return executor, adapter, log, load_world(scene)


def test_the_go2_stands_on_the_floor_with_its_own_camera():
    adapter = SimAdapter(LAB, robot="go2")
    assert adapter.profile is GO2 and adapter.capabilities == GO2.capabilities
    feet = [mujoco.mj_name2id(adapter.model, mujoco.mjtObj.mjOBJ_GEOM, n) for n in ("FL", "FR", "RL", "RR")]
    for g in feet:
        assert g >= 0 and abs(float(adapter.data.geom_xpos[g][2])) < 0.03  # feet at floor level in the home pose
    assert mujoco.mj_name2id(adapter.model, mujoco.mjtObj.mjOBJ_CAMERA, "head") >= 0
    assert len(adapter._robot_geoms) > 20  # the whole dog, not just the base, collides with obstacles
    adapter.close()


async def test_inspection_round_completes_on_the_go2(tmp_path):
    executor, adapter, log, state = make(LAB, record_dir=tmp_path)
    result = await executor.run(load_plan("plans/demo_inspection_round.yaml"), state)
    adapter.close()
    assert result.ok and result.steps_completed == 9
    assert result.final_state.robot.pose.distance_to(Pose2D(x=0, y=0)) <= 0.15
    assert [r.data["anomalies"] for r in log.find("perception.result")] == [[], ["present:red_box"]]


async def test_a_delivery_plan_is_refused_on_the_go2():
    executor, adapter, log, state = make(WAREHOUSE)
    result = await executor.run(load_plan("plans/demo_material_runner.yaml"), state)
    adapter.close()
    assert result.status == "invalid_plan" and "needs arm" in result.reason
    assert adapter.ticks == 0 and log.count("plan.invalid") == 1


async def test_the_wall_stops_the_go2_too():
    executor, adapter, log, state = make(BLOCKED)
    plan = TaskPlan(id="to_b", steps=[Step(skill="navigate", params={"to": "workstation_B"})])
    result = await executor.run(plan, state)
    adapter.close()
    assert result.status == "aborted" and adapter.blocked_by is not None and adapter.blocked_by.startswith("obs_")


async def test_arm_commands_raise_on_the_go2():
    adapter = SimAdapter(LAB, robot="go2")
    with pytest.raises(CapabilityMissing):
        await adapter.gripper("right", "close")
    with pytest.raises(CapabilityMissing):
        await adapter.move_arm("right", Pose3D(x=0.3, y=0.0, z=0.3), duration_s=0.1)
    assert adapter.ticks == 0  # refused before any motion, not after
    adapter.close()
