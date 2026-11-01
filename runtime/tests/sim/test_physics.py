"""Physics locomotion (ADR-0015): the robots walk with learned controllers, on floors and outdoor ground, and the
runtime sees what a real robot would give it: an approximate pose, a blocked path, a fall."""

from pathlib import Path

import numpy as np
import pytest

from spingi.adapters.sim_mujoco import SimAdapter
from spingi.core.human import ScriptedHuman
from spingi.core.ports import RobotFell
from spingi.core.types import Pose2D
from spingi.episode import read_manifest, read_trajectory
from spingi.replay import replay
from spingi.session import SessionConfig, run_session

LAB = Path("sim/scenes/lab_small.yaml")
BLOCKED = Path("sim/scenes/lab_blocked.yaml")
YARD = Path("sim/scenes/yard_outdoor.yaml")
TRACK = Path("sim/scenes/test_track.yaml")


def yaw_error(a: float, b: float) -> float:
    return abs((a - b + np.pi) % (2 * np.pi) - np.pi)


@pytest.mark.parametrize("robot", ["g1", "go2"])
async def test_the_robot_walks_to_a_pose_moving_its_legs(robot):
    adapter = SimAdapter(LAB, robot=robot, locomotion="physics")
    target = Pose2D(x=2.0, y=1.0, yaw=1.57)
    await adapter.walk_to(target, max_speed=0.5)
    adapter.close()
    assert adapter.blocked_by is None and adapter.fell is None
    assert adapter.pose.distance_to(target) < 0.15 and yaw_error(adapter.pose.yaw, target.yaw) < 0.15
    knees = [r["joints"][next(n for n in r["joints"] if "knee" in n or "calf" in n)] for r in adapter.trajectory]
    assert np.std(knees) > 0.05  # the legs moved: it walked, it did not glide
    assert adapter.battery_pct < 100 and 3.0 < adapter.sim_time_s < 30.0


async def test_the_go2_turns_on_the_spot_with_its_manoeuvring_policy():
    adapter = SimAdapter(LAB, robot="go2", locomotion="physics")
    await adapter.walk_to(Pose2D(x=0.0, y=0.0, yaw=3.0), max_speed=0.5)
    adapter.close()
    assert yaw_error(adapter.pose.yaw, 3.0) < 0.15 and adapter.pose.distance_to(Pose2D(x=0, y=0)) < 0.15
    assert adapter.walker.switches >= 1


async def test_the_go2_walks_up_the_ramp_onto_the_loading_dock():
    adapter = SimAdapter(YARD, robot="go2", locomotion="physics", start=Pose2D(x=11.0, y=4.5, yaw=0.0))
    await adapter.walk_to(Pose2D(x=17.5, y=4.5, yaw=0.0), max_speed=0.6)
    adapter.close()
    assert adapter.blocked_by is None and adapter.fell is None
    assert adapter.pose.x > 17.3 and adapter.base_z > 0.45 + 0.2  # standing on the dock, 45 cm up


async def test_the_go2_steps_up_the_kerb_of_the_test_track():
    # A 10 cm step with two metres of run-up at 0.6 m/s: measured 8 out of 8 (slower, it stubs its toes more often).
    adapter = SimAdapter(TRACK, robot="go2", locomotion="physics", start=Pose2D(x=36.8, y=0.0, yaw=0.0))
    await adapter.walk_to(Pose2D(x=41.5, y=0.0, yaw=0.0), max_speed=0.6)
    adapter.close()
    assert adapter.fell is None and adapter.pose.x > 41.3 and adapter.base_z > 0.1 + 0.2


async def test_an_obstacle_in_the_way_is_a_contact_that_blocks_the_walk():
    adapter = SimAdapter(BLOCKED, robot="g1", locomotion="physics")
    await adapter.walk_to(Pose2D(x=6.0, y=0.5, yaw=0.0), max_speed=0.5)
    adapter.close()
    assert adapter.blocked_by is not None and adapter.blocked_by.startswith("obs_")
    assert adapter.pose.x < 6.0 and adapter.fell is None


async def test_a_robot_pushed_over_falls_and_refuses_motion_until_picked_up():
    adapter = SimAdapter(LAB, robot="g1", locomotion="physics")
    adapter.data.xfrc_applied[adapter._root_body, 1] = 400.0  # a hard shove sideways, held
    with pytest.raises(RobotFell, match="fell at"):
        await adapter.walk_to(Pose2D(x=3.0, y=0.0, yaw=0.0), max_speed=0.5)
    assert adapter.estopped and adapter.mode == "estop"
    adapter.data.xfrc_applied[:] = 0.0
    with pytest.raises(RobotFell):
        await adapter.walk_to(Pose2D(x=0.0, y=0.0, yaw=0.0), max_speed=0.5)
    adapter.close()


async def test_kinematic_mode_follows_the_height_of_the_ground():
    adapter = SimAdapter(YARD, robot="go2", start=Pose2D(x=11.0, y=4.5, yaw=0.0))
    await adapter.walk_to(Pose2D(x=18.0, y=4.5, yaw=0.0), max_speed=1.0)
    adapter.close()
    assert adapter.pose.x == pytest.approx(18.0) and adapter.base_z == pytest.approx(0.45 + 0.27, abs=0.03)
    assert "z" in adapter.trajectory[-1]["robot"] and "joints" not in adapter.trajectory[-1]


async def test_a_physics_session_records_legs_heights_and_the_ground_and_replays_the_same(tmp_path):
    cfg = SessionConfig(
        plan=Path("plans/demo_inspection_round.yaml"),
        scene=LAB,
        adapter="sim",
        robot="go2",
        locomotion="physics",
        runs_dir=tmp_path,
    )
    result = await run_session(cfg, ScriptedHuman(default="abort"))
    assert result.ok, result.status
    manifest = read_manifest(result.run_dir)
    assert manifest.config.locomotion == "physics" and manifest.sample_rate_hz == 25.0
    samples = read_trajectory(result.run_dir)
    assert samples[-1].joints and samples[-1].robot.z is not None
    assert not (result.run_dir / "terrain.json").exists()  # a flat floor needs no ground for the viewer
    report = await replay(result.run_dir, runs_dir=tmp_path / "replayed")
    assert report.same, report.differences


async def test_an_outdoor_session_writes_the_ground_for_the_viewer(tmp_path):
    cfg = SessionConfig(plan=Path("plans/yard_patrol.yaml"), scene=YARD, adapter="sim", robot="go2", runs_dir=tmp_path)
    result = await run_session(cfg, ScriptedHuman(default="abort"))
    assert result.ok, result.status  # kinematic: the base follows the ground
    terrain = (result.run_dir / "terrain.json").read_text()
    assert '"surfaces"' in terrain and '"heights_mm"' in terrain
    assert "terrain.json" in read_manifest(result.run_dir).files


async def test_a_walking_g1_picks_without_being_moved():
    adapter = SimAdapter(Path("sim/scenes/warehouse_small.yaml"), robot="g1", locomotion="physics")
    await adapter.walk_to(Pose2D(x=4.0, y=0.0, yaw=1.57), max_speed=0.5)
    await adapter.walk_to(Pose2D(x=4.0, y=3.0, yaw=0.0), max_speed=0.5)
    before = (adapter.pose, adapter.data.qpos[7:].copy())
    grip = await adapter.gripper("right", "close")
    adapter.close()
    assert grip.holding and grip.object_id == "red_box_01"
    assert adapter.pose == before[0] and np.allclose(adapter.data.qpos[7:], before[1])  # no reset, no teleport
