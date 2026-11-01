"""What the simulated robot knows (ADR-0016): a pose estimate that drifts, corrected by landmarks, and a head camera
with the defects of a real one. Physics outcomes asserted here were measured on 8 seeds out of 8."""

import math
from pathlib import Path

import numpy as np
import pytest

from spingi.adapters.sim_mujoco import SimAdapter
from spingi.core.events import EventLog
from spingi.core.human import ScriptedHuman
from spingi.core.localization import yaw_of
from spingi.core.types import Pose2D
from spingi.episode import read_manifest, read_trajectory
from spingi.perception.markers import MarkerPerceiver
from spingi.perception.sim import SimPerceiver
from spingi.scenes import load_world
from spingi.session import SessionConfig, run_session

LAB = Path("sim/scenes/lab_small.yaml")
YARD = Path("sim/scenes/yard_outdoor.yaml")


def rendering(adapter: SimAdapter, frame) -> None:
    if adapter.image(frame.id) is None:
        adapter.close()
        pytest.skip(f"no rendering: {adapter.render_error}")


def test_drift_needs_a_walking_robot():
    with pytest.raises(ValueError, match="physics"):
        SimAdapter(LAB, odometry="drift")


async def test_without_drift_the_robot_knows_where_it_is():
    adapter = SimAdapter(LAB, robot="go2", locomotion="physics")
    await adapter.walk_to(Pose2D(x=2.0, y=0.5, yaw=0.0), max_speed=0.6)
    adapter.close()
    assert await adapter.get_pose() == adapter.pose


@pytest.mark.parametrize("seed", [0, 1])
async def test_a_walking_robot_drifts_and_a_correction_puts_it_back(seed):
    log = EventLog(run_id="r-drift")
    adapter = SimAdapter(YARD, robot="go2", locomotion="physics", odometry="drift", seed=seed, log=log)
    await adapter.walk_to(Pose2D(x=8.0, y=0.0, yaw=0.0), max_speed=0.8)
    believed = await adapter.get_pose()
    assert believed.distance_to(adapter.pose) > 0.01  # it believes it is somewhere it is not
    arrival = log.find("sim.arrival")[-1].data
    assert arrival["error_m"] == pytest.approx(believed.distance_to(adapter.pose), abs=1e-3)
    await adapter.relocalize(adapter.pose)  # a perfect landmark
    assert (await adapter.get_pose()).distance_to(adapter.pose) < 1e-9
    target = Pose2D(x=9.0, y=0.0, yaw=0.0)
    await adapter.walk_to(target, max_speed=0.6)
    adapter.close()
    assert adapter.pose.distance_to(target) < 0.2  # the corrected frame is the real one again (worst of 8: 0.11)
    assert "estimate" in adapter.trajectory[-1] or adapter.pose.distance_to(await adapter.get_pose()) < 1e-4


async def test_a_kinematic_robot_walks_in_the_frame_it_was_told():
    adapter = SimAdapter(LAB)
    await adapter.relocalize(Pose2D(x=1.0, y=0.0, yaw=0.0))  # it is at the origin, it believes it is 1 m ahead
    await adapter.walk_to(Pose2D(x=2.0, y=0.0, yaw=0.0), max_speed=0.5)
    adapter.close()
    assert adapter.pose.distance_to(Pose2D(x=1.0, y=0.0)) < 1e-6
    assert (await adapter.get_pose()).distance_to(Pose2D(x=2.0, y=0.0)) < 1e-6


async def test_the_camera_is_placed_where_the_robot_believes_it_is():
    adapter = SimAdapter(LAB)
    true_model = (await adapter.get_camera("head")).camera_model
    await adapter.relocalize(Pose2D(x=adapter.pose.x + 1.0, y=adapter.pose.y, yaw=adapter.pose.yaw))
    believed_model = (await adapter.get_camera("head")).camera_model
    adapter.close()
    assert believed_model.position[0] == pytest.approx(true_model.position[0] + 1.0)
    assert believed_model.rotation == pytest.approx(true_model.rotation)


async def test_a_noisy_camera_sends_jpeg_frames_unlike_the_clean_render(tmp_path):
    clean = SimAdapter(LAB, robot="go2")
    noisy = SimAdapter(LAB, robot="go2", camera_noise=True, record_dir=tmp_path)
    a, b = await clean.get_camera("head"), await noisy.get_camera("head")
    rendering(clean, a)
    img_a, img_b = clean.image(a.id).astype(float), noisy.image(b.id).astype(float)
    clean.close()
    noisy.close()
    assert b.data_ref.endswith(".jpg") and (tmp_path / b.data_ref).read_bytes()[:2] == b"\xff\xd8"
    assert 1.0 < float(np.abs(img_a - img_b).mean()) < 20.0  # the same view, degraded
    assert b.camera_model.fx != a.camera_model.fx  # the robot's calibration is slightly off


@pytest.mark.parametrize("robot", ["g1", "go2"])
async def test_a_landmark_is_read_from_the_noisy_frame_with_the_direction_it_faces(robot):
    lm = next(lm for lm in load_world(LAB).landmarks if lm.marker_id == 10)  # on the column, seen from the dock
    adapter = SimAdapter(LAB, robot=robot, camera_noise=True)
    frame = await adapter.get_camera("head")
    rendering(adapter, frame)
    seen = await MarkerPerceiver.for_adapter(adapter, LAB).localize(frame, lm.marker_id)
    adapter.close()
    assert seen is not None and math.hypot(seen.x - lm.pose.x, seen.y - lm.pose.y) < 0.05
    assert abs((yaw_of(seen) - lm.pose.yaw + math.pi) % (2 * math.pi) - math.pi) < 0.05


async def test_the_truth_perceiver_reports_landmarks_where_the_robot_believes_they_are():
    lm = next(lm for lm in load_world(LAB).landmarks if lm.marker_id == 10)
    adapter = SimAdapter(LAB)
    await adapter.relocalize(Pose2D(x=0.0, y=0.5, yaw=0.0))  # it believes it is half a metre to the left
    seen = await SimPerceiver(adapter).localize(await adapter.get_camera("head"), lm.marker_id)
    adapter.close()
    assert seen is not None and seen.x == pytest.approx(lm.pose.x) and seen.y == pytest.approx(lm.pose.y + 0.5)


async def test_the_head_camera_draws_what_is_close_in_a_large_scene():
    # The pump cabinet 40 cm in front of the Go2's camera, in a 40 m yard (the cameras' clipping planes in metres).
    adapter = SimAdapter(YARD, robot="go2", start=Pose2D(x=20.9, y=-1.0, yaw=0.0))
    frame = await adapter.get_camera("head")
    rendering(adapter, frame)
    centre = adapter.image(frame.id)[400:560, 560:720].astype(float).mean(axis=(0, 1))
    adapter.close()
    assert centre[2] > centre[0] + 20  # the cabinet's blue, not the dirt behind it


async def test_a_realistic_inspection_round_corrects_its_pose_with_the_landmarks(tmp_path):
    cfg = SessionConfig(
        plan=Path("plans/demo_inspection_round.yaml"),
        scene=LAB,
        adapter="sim",
        robot="go2",
        perception="markers",
        locomotion="physics",
        odometry="drift",
        camera_noise=True,
        runs_dir=tmp_path,
    )
    result = await run_session(cfg, ScriptedHuman(default="abort"))
    assert result.ok and result.log.count("robot.relocalized") >= 4
    assert max(e.data["error_m"] for e in result.log.find("sim.arrival")) < 0.8  # worst of 8 seeds: 0.50 m
    assert all(p.suffix == ".jpg" for p in (result.run_dir / "frames").iterdir())
    assert any(s.estimate is not None for s in read_trajectory(result.run_dir))
    config = read_manifest(result.run_dir).config
    assert (config.odometry, config.camera_noise) == ("drift", True)
