"""Scenario tests: a whole plan in the MuJoCo scene, assertions on the final WorldState and on the events."""

from pathlib import Path

import pytest

from spingi.adapters.sim_mujoco import SimAdapter
from spingi.core.events import EventLog
from spingi.core.executor import Executor
from spingi.core.human import ScriptedHuman
from spingi.core.plan import Step, TaskPlan, load_plan
from spingi.core.types import Pose2D
from spingi.perception.fake import FakePerceiver
from spingi.perception.sim import SimPerceiver
from spingi.safety import SafetyMonitor
from spingi.scenes import load_safety_limits, load_world
from spingi.skills import default_registry

LAB = Path("sim/scenes/lab_small.yaml")
BLOCKED = Path("sim/scenes/lab_blocked.yaml")
FENCED = Path("sim/scenes/lab_geofence.yaml")


def make(scene: Path, human: ScriptedHuman | None = None, sim_perceiver: bool = False, **kw):
    adapter = SimAdapter(scene, **kw)
    log = EventLog(run_id="r-sim")
    executor = Executor(
        registry=default_registry(),
        robot=adapter,
        perceiver=SimPerceiver(adapter) if sim_perceiver else FakePerceiver(),
        human=human or ScriptedHuman(default="abort"),
        log=log,
    )
    return executor, adapter, log, load_world(scene)


async def test_inspection_round_completes_in_sim(tmp_path):
    executor, adapter, log, state = make(LAB, sim_perceiver=True, record_dir=tmp_path)
    result = await executor.run(load_plan("plans/demo_inspection_round.yaml"), state)
    adapter.close()
    assert result.ok and result.steps_completed == 9
    assert result.final_state.robot.pose.distance_to(Pose2D(x=0, y=0)) <= 0.15
    assert log.count("human.request") == 0
    assert 20 < adapter.sim_time_s < 120  # ~17 m at 0.5 m/s plus the turns
    assert result.final_state.robot.battery_pct < 100
    # the shelf check sees the box, the panel check does not: one anomaly, and the box enters the world state
    results = log.find("perception.result")
    assert [r.data["anomalies"] for r in results] == [[], ["present:red_box"]]
    assert "red_box_01" in result.final_state.objects


async def test_geofence_stops_the_robot_and_runtime_escalates():
    human = ScriptedHuman(default="abort")
    executor, adapter, log, state = make(FENCED, human=human)
    monitor = SafetyMonitor(adapter, log, load_safety_limits(FENCED), period_s=0.0)
    await monitor.start()
    plan = TaskPlan(
        id="out",
        steps=[Step(skill="navigate", params={"to": "workstation_B", "max_speed": 2.0}, on_failure={"retry": 1})],
    )
    result = await executor.run(plan, state)
    await monitor.stop()
    adapter.close()
    assert result.status == "aborted"
    assert monitor.tripped and log.count("safety.geofence") >= 1
    assert adapter.pose.x < 4.6  # stopped within one check period past the fence
    assert adapter.speed_cap == 0.8 and log.count("safety.speed_capped") == 1
    assert len(human.requests) == 1


async def test_wall_blocks_the_robot_and_runtime_escalates():
    human = ScriptedHuman(default="abort")
    executor, adapter, log, state = make(BLOCKED, human=human)
    plan = TaskPlan(
        id="to_b",
        steps=[Step(skill="navigate", params={"to": "workstation_B"}, on_failure={"retry": 1, "then": "needs_human"})],
    )
    result = await executor.run(plan, state)
    adapter.close()
    assert result.status == "aborted"
    assert adapter.blocked_by == "obs_0"
    assert adapter.pose.x < 3.0  # stopped before the wall
    assert log.count("skill.postcondition_failed") == 2  # first attempt + 1 retry
    assert len(human.requests) == 1 and human.requests[0].skill == "navigate"


async def test_speed_cap_bounds_simulated_time():
    executor, adapter, log, state = make(LAB, speed_cap=0.25)
    plan = TaskPlan(id="fast", steps=[Step(skill="navigate", params={"to": "workstation_B", "max_speed": 2.0})])
    result = await executor.run(plan, state)
    adapter.close()
    assert result.ok and adapter.last_applied_speed == 0.25
    assert adapter.sim_time_s >= 6.0 / 0.25 * 0.95


async def test_head_camera_renders_when_gl_available(tmp_path):
    adapter = SimAdapter(LAB, record_dir=tmp_path)
    frame = await adapter.get_camera()
    adapter.close()
    if frame.data_ref is None:
        pytest.skip("rendering unavailable (no GL context)")
    assert Path(frame.data_ref).exists() and Path(frame.data_ref).stat().st_size > 1000


async def test_recording_writes_a_video(tmp_path):
    adapter = SimAdapter(LAB, record_dir=tmp_path, record_video=True, record_every=10)
    await adapter.walk_to(Pose2D(x=1.0, y=0.0, yaw=0.0), max_speed=1.0)
    video = adapter.close()
    if video is None:
        pytest.skip("rendering unavailable (no GL context)")
    assert video.exists() and video.stat().st_size > 1000
