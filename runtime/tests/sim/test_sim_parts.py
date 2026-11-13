"""The parts SimAdapter delegates to (ADR-0020), each tested alone on the lab scene: what the robot touches, the
object in its hand, the camera it believes it has, the videos of a run."""

import math
from pathlib import Path

import mujoco
import numpy as np
import pytest

from spingi.adapters.sim_mujoco import SimAdapter
from spingi.adapters.sim_mujoco.contacts import ground_geoms, touched_obstacle
from spingi.adapters.sim_mujoco.objects import SceneObjects
from spingi.adapters.sim_mujoco.rendering import HeadCamera, Renderers, Videos
from spingi.core.ports import CapabilityMissing
from spingi.core.types import Pose2D

LAB = Path("sim/scenes/lab_small.yaml")  # red_box_01 at (0, 2.9) on shelf A (obs_1, top 0.85 m); a column at (3, -1.5)


@pytest.fixture
def g1():
    adapter = SimAdapter(LAB)
    yield adapter
    adapter.close()


def objects_of(adapter: SimAdapter) -> SceneObjects:
    return SceneObjects(adapter.model, adapter.data, adapter.scene, adapter.profile)


# --- contacts ---------------------------------------------------------------------------------------------------------
def test_the_ground_is_neither_the_robot_nor_an_obstacle(g1):
    ground = ground_geoms(g1.model)
    assert ground
    assert not ground & g1._robot_geoms
    assert touched_obstacle(g1.model, g1.data, g1._robot_geoms, ground) is None  # standing on the floor at the dock


def test_the_robot_touches_the_obstacle_it_is_placed_into(g1):
    g1.pose = Pose2D(x=3.0, y=-1.5, yaw=0.0)  # inside the column
    g1._apply_pose()
    assert touched_obstacle(g1.model, g1.data, g1._robot_geoms, ground_geoms(g1.model)) == "obs_0"


# --- objects ----------------------------------------------------------------------------------------------------------
def test_a_grasp_takes_the_nearest_object_within_reach_into_the_hand(g1):
    objects = objects_of(g1)
    facing_the_shelf = Pose2D(x=0.0, y=2.5, yaw=math.pi / 2)
    assert objects.grasp(facing_the_shelf, range_m=0.9)
    assert objects.held == "red_box_01"
    box = objects.positions()["red_box_01"]
    assert (box["x"], box["y"], box["z"]) == pytest.approx((0.0, 2.5 + 0.35, 0.95), abs=1e-3)  # the G1's hand


def test_nothing_is_grasped_out_of_reach(g1):
    objects = objects_of(g1)
    assert not objects.grasp(Pose2D(x=0.0, y=0.0, yaw=math.pi / 2), range_m=0.9)
    assert objects.held is None
    assert objects.positions()["red_box_01"]["z"] == pytest.approx(0.9, abs=1e-3)  # still on the shelf


def test_a_released_object_rests_on_the_obstacle_below_the_hand_or_else_on_the_floor(g1):
    objects = objects_of(g1)
    over_the_shelf = Pose2D(x=0.0, y=2.5, yaw=math.pi / 2)  # the hand at y 2.85, over the shelf (2.7 to 3.1)
    objects.grasp(over_the_shelf, range_m=0.9)
    on_shelf = objects.release(over_the_shelf)
    assert objects.held is None
    assert on_shelf.z == pytest.approx(0.85 + 0.05, abs=1e-3)  # the shelf's top plus half the box

    short_of_the_shelf = Pose2D(x=0.0, y=2.2, yaw=math.pi / 2)  # the hand at y 2.55: nothing below it
    objects.grasp(short_of_the_shelf, range_m=0.9)
    on_floor = objects.release(short_of_the_shelf)
    assert (on_floor.y, on_floor.z) == pytest.approx((2.55, 0.05), abs=1e-3)


def test_the_held_object_follows_the_hand(g1):
    objects = objects_of(g1)
    objects.grasp(Pose2D(x=0.0, y=2.5, yaw=math.pi / 2), range_m=0.9)
    objects.follow_hand(Pose2D(x=1.0, y=1.0, yaw=0.0))
    mujoco.mj_forward(g1.model, g1.data)
    box = objects.positions()["red_box_01"]
    assert (box["x"], box["y"]) == pytest.approx((1.35, 1.0), abs=1e-3)


def test_a_robot_without_an_arm_cannot_hold_an_object():
    go2 = SimAdapter(LAB, robot="go2")
    try:
        with pytest.raises(CapabilityMissing):
            objects_of(go2).grasp(Pose2D(x=0.0, y=2.5, yaw=math.pi / 2), range_m=0.9)
    finally:
        go2.close()


# --- the head camera --------------------------------------------------------------------------------------------------
def head_camera(adapter: SimAdapter, noisy: bool = False, seed: int = 0) -> HeadCamera:
    return HeadCamera(
        Renderers(adapter.model, adapter.data), adapter.model, adapter.data, (320, 240), noisy, seed, None
    )


def test_a_frame_places_the_camera_where_the_robot_believes_it_is(g1):
    camera = head_camera(g1)
    cam = mujoco.mj_name2id(g1.model, mujoco.mjtObj.mjOBJ_CAMERA, "head")
    truth = np.array(g1.data.cam_xpos[cam])

    knows = camera.take("head", ts=0.0, offset=(0.0, 0.0, 0.0)).camera_model
    assert knows.position == pytest.approx(tuple(truth))
    assert (knows.cx, knows.cy) == (160, 120)

    shifted = camera.take("head", ts=0.0, offset=(1.0, 2.0, 0.0)).camera_model
    assert shifted.position == pytest.approx(tuple(truth + np.array([1.0, 2.0, 0.0])))

    turned = camera.take("head", ts=0.0, offset=(0.0, 0.0, math.pi / 2)).camera_model
    assert turned.position == pytest.approx((-truth[1], truth[0], truth[2]))


def test_frames_are_numbered_and_an_unknown_camera_has_no_model(g1):
    camera = head_camera(g1)
    first, second = camera.take("head", 0.0, (0.0, 0.0, 0.0)), camera.take("nowhere", 0.5, (0.0, 0.0, 0.0))
    assert (first.id, second.id, second.ts) == ("sim-1", "sim-2", 0.5)
    assert second.camera_model is None


def test_a_noisy_camera_believes_in_a_focal_length_slightly_off_the_same_for_the_same_seed(g1):
    def focal(noisy: bool, seed: int) -> float:
        return head_camera(g1, noisy, seed).take("head", 0.0, (0.0, 0.0, 0.0)).camera_model.fx

    clean = focal(False, 0)
    assert focal(True, 0) != clean
    assert focal(True, 0) == focal(True, 0)
    assert focal(True, 0) != focal(True, 1)
    assert abs(focal(True, 0) / clean - 1) < 0.1


# --- videos -----------------------------------------------------------------------------------------------------------
def test_no_video_is_written_without_an_episode_folder(g1):
    videos = Videos(Renderers(g1.model, g1.data), None, fps=10.0, third_person=True, head=True)
    videos.add(0.0)
    assert (videos.third_person, videos.head) == (None, None)
    assert videos.head_for_manifest() is None
    assert videos.close() is None
